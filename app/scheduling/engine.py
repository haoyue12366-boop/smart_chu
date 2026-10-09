"""内存编排：共享截止时间内只选择已通过独立校验的完整候选。"""

import time

from app.domain.knowledge import MenuKnowledgeView
from app.domain.objectives import ObjectiveStage
from app.domain.ports import CpSatScheduler as SolverPort
from app.domain.ports import Deadline, ScheduleValidator
from app.domain.reports import PhaseTiming, PlanningFailure, PlanningResult, SolveResult
from app.domain.runtime_snapshot import RuntimeSnapshot
from app.domain.schedule import CandidateSchedule, ValidatedSchedule
from app.domain.scheduling_problem import SchedulingProblem
from app.scheduling.budget import ComputationBudget
from app.scheduling.candidate_pool import CandidatePool
from app.scheduling.cp_sat import CpSatScheduler
from app.scheduling.greedy import GreedyScheduler
from app.scheduling.heating_compaction import compact_heating_slack
from app.scheduling.human_phases import human_phase_count
from app.scheduling.metrics import objective_spread
from app.scheduling.objectives import makespan_cap
from app.scheduling.reverse_hints import align_cooking_finishes
from app.scheduling.serial_reference import serial_order_holds
from app.scheduling.tail_compaction import compact_cooking_tails


class PlanningEngine:
    def __init__(
        self,
        *,
        validator: ScheduleValidator,
        solver: SolverPort | None = None,
        previous_plan: CandidateSchedule | None = None,
        computation_budget: ComputationBudget | None = None,
    ) -> None:
        self.validator = validator
        self.solver: SolverPort = solver if solver is not None else CpSatScheduler()
        self.previous_plan = previous_plan
        self.computation_budget = computation_budget

    def plan(
        self,
        problem: SchedulingProblem,
        knowledge: MenuKnowledgeView,
        runtime: RuntimeSnapshot,
        deadline: Deadline,
    ) -> PlanningResult:
        started = time.monotonic_ns()
        if runtime != problem.runtime:
            return PlanningResult(
                status="FAILED",
                failure=PlanningFailure(
                    code="STATE_CONFLICT",
                    failure_class="STALE_STATE",
                    message="运行状态在本轮编译后已变化",
                ),
            )
        budget = (
            problem.policy.replan_budget
            if (
                runtime.details.planning_kind == "REPLAN"
                if runtime.details
                else bool(runtime.now_offset_sec)
            )
            else problem.policy.initial_budget
        )
        compute_end = deadline.expires_at_ns - budget.publication_reserve_ms * 1_000_000
        if started >= compute_end:
            return PlanningResult(
                status="FAILED",
                failure=PlanningFailure(
                    code="NO_FEASIBLE_PLAN",
                    failure_class="NO_SOLUTION_WITHIN_BUDGET",
                    message="扣除最终校验预留后没有计算预算",
                ),
            )
        pool = CandidatePool(problem, knowledge, runtime, self.validator, self.previous_plan)
        phases: list[SolveResult] = []
        accepted: set[str] = set()
        accepted_plans: set[str] = set()
        first_validated_ms: int | None = None
        serial: ValidatedSchedule | None = None
        reverse_timings: list[PhaseTiming] = []
        cap = None
        human_optimized = False
        total_human_optimized = False
        stability_optimized = False
        allowance = self.computation_budget or ComputationBudget.from_spec(budget)
        quality_first = problem.policy.quality_first
        solver_end = compute_end

        def accept_candidate(
            candidate: CandidateSchedule, stage: ObjectiveStage | None = None
        ) -> bool:
            nonlocal compute_end, solver_end
            accepted_candidate = pool.add(candidate, stage)
            # 发布还需独立扫描候选和串行参考，并写入预约、通知与回执。
            # 静态预留覆盖存储，扫描预留按本请求实测开销增加；只缩短可选优化，
            # 不扩大请求截止，也不复用旧 proof 跳过发布校验。
            scan_ns = pool.max_validation_elapsed_ns
            compute_end = min(
                compute_end,
                deadline.expires_at_ns - budget.publication_reserve_ms * 1_000_000 - scan_ns * 2,
            )
            # 最后一个 Solver 候选也要进入独立校验池，不能占用发布的时间。
            solver_end = min(solver_end, compute_end - scan_ns * 2)
            return accepted_candidate

        greedy_started = time.monotonic_ns()
        try:
            greedy_limit = Deadline(
                expires_at_ns=min(compute_end, greedy_started + allowance.greedy_allowance_ns)
            )
            extension = None
            if (
                quality_first
                and self.previous_plan is not None
                and (
                    {item.recipe_instance_id for item in self.previous_plan.recipe_completions}
                    < {item.recipe_instance_id for item in problem.recipe_instances}
                )
            ):
                extension = GreedyScheduler().solve_extension(
                    problem, self.previous_plan, greedy_limit
                )
            construct = (
                GreedyScheduler().solve_serial
                if quality_first
                and not problem.fixed_executions
                and not problem.fixed_supply_fulfillments
                and not problem.advance_preparations
                else GreedyScheduler().solve
            )
            greedy = (
                extension
                if extension is not None and extension.candidate is not None
                else construct(problem, greedy_limit)
            )
        finally:
            allowance.charge_greedy(time.monotonic_ns() - greedy_started)
        if greedy.candidate is not None and accept_candidate(greedy.candidate):
            first_validated_ms = (time.monotonic_ns() - started) // 1_000_000
        fast_replan = (
            problem.policy.replan_search_mode == "FEASIBILITY_FIRST"
            and runtime.details is not None
            and runtime.details.planning_kind == "REPLAN"
        )
        if not problem.logical_tasks and pool.values:
            # 取消全部需求后没有优化变量，仍以实际编译、构造和独立校验结果发布。
            empty = next(iter(pool.values.values()))
            return PlanningResult(
                status="VALIDATED",
                candidate=empty.candidate,
                validation=empty.validation,
                selected_candidate_source="GREEDY",
                first_validated_candidate_ms=first_validated_ms,
                serial_reference_candidate=empty.candidate,
                serial_reference_validation=empty.validation,
                timings=greedy.timings,
            )
        solver_end = min(solver_end, time.monotonic_ns() + allowance.solver_remaining_ns)

        def solve(
            stage: ObjectiveStage | None,
            cutoff: int,
            *,
            serial_menu: bool = False,
            seed_hint: ValidatedSchedule | None = None,
        ) -> SolveResult | None:
            nonlocal first_validated_ms
            if time.monotonic_ns() >= min(cutoff, solver_end) or allowance.solver_remaining_ns <= 0:
                return None
            best = pool.best(cap, makespan_first=stage is None or stage.name == "A_MAKESPAN")
            hint = (
                seed_hint.candidate if seed_hint is not None else best.candidate if best else None
            )
            if (
                quality_first
                and stage is not None
                and stage.name == "E_QUALITY"
                and self.previous_plan is not None
                and not problem.fixed_executions
                and ("HUMAN_BUSY" not in problem.policy.objective.stages or best is None)
            ):
                carriers = {
                    carrier.carrier_id
                    for carrier in (
                        *problem.standalone_candidates,
                        *problem.shared_prep_candidates,
                        *problem.thermal_batch_candidates,
                        *problem.inventory_supply_candidates,
                    )
                }
                common = tuple(
                    a for a in self.previous_plan.assignments if a.carrier_id in carriers
                )
                covered = {task for a in common for task in a.task_ids}
                if common and covered < {task.task_id for task in problem.logical_tasks}:
                    # 新加菜先沿用仍存在工序的旧时间提示；仅是部分搜索提示，
                    # 不加入候选池、不冻结旧计划，新菜的选择与时间保持自由。
                    hint = CandidateSchedule(problem_hash=problem.problem_hash, assignments=common)
            solve_started = time.monotonic_ns()
            try:
                result = self.solver.solve(
                    problem,
                    hint,
                    Deadline(
                        expires_at_ns=min(
                            cutoff, solver_end, solve_started + allowance.solver_remaining_ns
                        )
                    ),
                    stage=stage,
                    serial_menu=serial_menu,
                )
            finally:
                allowance.charge_solver(time.monotonic_ns() - solve_started)
            phases.append(result)
            if result.candidate is not None and time.monotonic_ns() < compute_end:
                if accept_candidate(result.candidate, stage):
                    accepted.add(result.candidate.candidate_hash)
                    accepted_plans.add(
                        result.candidate.model_copy(update={"metrics": None}).candidate_hash
                    )
                    if first_validated_ms is None:
                        first_validated_ms = (time.monotonic_ns() - started) // 1_000_000
            return result

        # 参考构造与所有优化阶段共享同一个 Solver 总预算。
        # 完整热批次的进程传输和建模也占预算；过短的首阶段会终止
        # 健康工作进程，令后续阶段反复冷启动。串行参考是比赛 timeSave 的
        # 必需证据。快速候选已合法时只补齐参考，使用剩余 Solver 额度；
        # 构造失败或默认优化仍为后续阶段保留余额，不因阶段拆分新增时间。
        reference_started = time.monotonic_ns()
        greedy_best = pool.best(makespan_first=True)
        if (
            (fast_replan or quality_first)
            and greedy_best is not None
            and serial_order_holds(greedy_best.candidate, problem)
        ):
            serial = greedy_best
        else:
            reference_end = (
                solver_end
                if fast_replan and greedy_best is not None
                else min(
                    solver_end,
                    time.monotonic_ns()
                    + min(
                        allowance.solver_remaining_ns
                        if fast_replan
                        else max(1_200_000_000, budget.solver_ms * 1_000_000 // 4),
                        max(0, solver_end - time.monotonic_ns()) * 2 // 3,
                    ),
                )
            )
            reference = solve(None, reference_end, serial_menu=True)
            if (
                reference is not None
                and reference.candidate is not None
                and serial_order_holds(reference.candidate, problem)
            ):
                serial = next(
                    (
                        v
                        for v in pool.values.values()
                        if v.candidate.assignments == reference.candidate.assignments
                    ),
                    None,
                )
            if quality_first and serial is None:
                # 只在参考求解未成功时补做有限构造；正常路径保留子进程基础模型缓存，
                # 不提前挤占质量阶段的预算。构造仍从同一 Solver 余额扣费。
                construction_started = time.monotonic_ns()
                reference_layout = GreedyScheduler().solve_serial(
                    problem,
                    Deadline(
                        expires_at_ns=min(
                            solver_end,
                            construction_started + max(400_000_000, budget.greedy_ms * 1_000_000),
                        )
                    ),
                )
                allowance.charge_solver(time.monotonic_ns() - construction_started)
                if (
                    reference_layout.candidate is not None
                    and serial_order_holds(reference_layout.candidate, problem)
                    and accept_candidate(reference_layout.candidate)
                ):
                    serial = next(
                        v
                        for v in pool.values.values()
                        if v.candidate.assignments == reference_layout.candidate.assignments
                    )
                reverse_timings.append(
                    PhaseTiming(
                        stage="SERIAL_CONSTRUCTION",
                        elapsed_ms=(time.monotonic_ns() - construction_started) // 1_000_000,
                    )
                )
        if fast_replan and greedy_best is not None and pool.values:
            timing = (
                *greedy.timings,
                *(t for phase in phases for t in phase.timings),
                PhaseTiming(
                    stage="SERIAL_REFERENCE",
                    elapsed_ms=(time.monotonic_ns() - reference_started) // 1_000_000,
                ),
                PhaseTiming(
                    stage="VALIDATION_AND_METRICS",
                    elapsed_ms=pool.validation_elapsed_ns // 1_000_000,
                ),
                PhaseTiming(
                    stage="PLANNING_ENGINE", elapsed_ms=(time.monotonic_ns() - started) // 1_000_000
                ),
            )
            if serial is None or time.monotonic_ns() >= compute_end:
                return PlanningResult(
                    status="FAILED",
                    failure=PlanningFailure(
                        code="STATE_INCOMPLETE",
                        failure_class="STATE_INCOMPLETE",
                        message="快速重排未在预算内取得当前状态的已验证串行参考",
                    ),
                    stage_results=tuple(phases),
                    rejected_candidates=tuple(pool.rejections),
                    timings=timing,
                )
            # 参考也进入合法候选池；选最短完整方案保证真实差值非负，省略可选质量阶段。
            best = pool.best(makespan_first=True)
            assert best is not None
            return PlanningResult(
                status="VALIDATED",
                candidate=best.candidate,
                validation=best.validation,
                first_validated_candidate_ms=first_validated_ms,
                selected_candidate_source="CP_SAT"
                if best.candidate.model_copy(update={"metrics": None}).candidate_hash
                in accepted_plans
                else "GREEDY",
                solve_result=phases[-1] if phases else None,
                stage_results=tuple(phases),
                serial_reference_candidate=serial.candidate,
                serial_reference_validation=serial.validation,
                rejected_candidates=tuple(pool.rejections),
                timings=(*timing, PhaseTiming(stage="FEEDBACK_FEASIBILITY_RETURN", elapsed_ms=0)),
            )
        current = time.monotonic_ns()
        if quality_first:
            # Solver 截止后仍需扫描新候选。按本问题已实际测到的扫描开销
            # 预留两倍时间，避免把 SQL 发布预留用在候选校验上。
            solver_end = min(
                solver_end, compute_end - max(150_000_000, pool.max_validation_elapsed_ns * 2)
            )
            # 串行参考只提供真实 timeSave 和总流程上界；不先优化并行
            # makespan、再用几段过短的 IPC 调用消耗质量目标的预算。
            cap = (
                serial.candidate.metrics.makespan_sec
                if serial and serial.candidate.metrics
                else None
            )
            if "HUMAN_BUSY" in problem.policy.objective.stages:
                # 人工连续段含顺序变量。先用较轻模型保留出锅达标的完整方案，
                # 再在同一预算中搜索连续人工，避免复杂阶段超时只剩串行方案。
                seed_started = time.monotonic_ns()
                seed_end = min(
                    solver_end,
                    seed_started + max(0, solver_end - seed_started) // 5,
                )
                solve(
                    ObjectiveStage(
                        name="C_MAKESPAN", makespan_cap_sec=cap, spread_excess_cap_sec=0
                    ),
                    seed_end,
                )
                seed = pool.best()
                if (
                    seed is None
                    or seed.candidate.metrics is None
                    or objective_spread(seed.candidate.metrics, problem)
                    > problem.policy.objective.spread_target_sec
                ):
                    solve(
                        ObjectiveStage(name="B_SPREAD", makespan_cap_sec=cap),
                        seed_started + max(0, solver_end - seed_started) * 2 // 5,
                    )
                    seed = pool.best()
                # 轻量阶段形成完整解后，先移动整个烹饪预约及关联后续。
                # 提示只围绕当前最晚出锅，且不增加现有总流程；独立校验后
                # 才交给含人工顺序变量的质量阶段，原完整候选始终保留。
                if seed is not None:
                    reverse_started = time.monotonic_ns()
                    aligned = align_cooking_finishes(
                        seed.candidate,
                        problem,
                        Deadline(expires_at_ns=min(solver_end, reverse_started + 100_000_000)),
                        self.previous_plan,
                    )
                    aligned = compact_heating_slack(
                        aligned,
                        problem,
                        Deadline(expires_at_ns=min(solver_end, reverse_started + 100_000_000)),
                        self.previous_plan,
                    )
                    if aligned != seed.candidate and time.monotonic_ns() < solver_end:
                        from_solver = (
                            seed.candidate.model_copy(update={"metrics": None}).candidate_hash
                            in accepted_plans
                        )
                        if accept_candidate(aligned):
                            if from_solver:
                                accepted_plans.add(
                                    aligned.model_copy(update={"metrics": None}).candidate_hash
                                )
                            # 相同极差/人工/总流程也可能有更紧的出锅布局；
                            # 保留已校验的具体提示，不用哈希平局丢掉它。
                            seed = next(
                                (
                                    v
                                    for v in pool.values.values()
                                    if v.candidate.assignments == aligned.assignments
                                ),
                                seed,
                            )
                    reverse_timings.append(
                        PhaseTiming(
                            stage="REVERSE_COOKING_HINT",
                            elapsed_ms=(time.monotonic_ns() - reverse_started) // 1_000_000,
                        )
                    )
                    allowance.charge_solver(time.monotonic_ns() - reverse_started)
                if seed and seed.candidate.metrics:
                    cap = makespan_cap(seed.candidate.metrics.makespan_sec, problem.policy, cap)
                    excess = max(
                        0,
                        objective_spread(seed.candidate.metrics, problem)
                        - problem.policy.objective.spread_target_sec,
                    )
                    if excess:
                        # 达标未果时，把一小段剩余预算专用于改善出锅差。
                        # 使用完整提示及现有流程界，不把难度转移为更长的整桌等待。
                        solve(
                            ObjectiveStage(
                                name="B_SPREAD",
                                makespan_cap_sec=cap,
                                spread_excess_cap_sec=excess,
                            ),
                            seed_started + max(0, solver_end - seed_started) * 4 // 5,
                            seed_hint=seed,
                        )
            # 先搜索达标区间，避免软惩罚在短时搜索中先找到很差的出菜差。
            # 没有取得独立合法解时再放松目标；UNKNOWN 不代表目标不可行。
            quality_spread_cap = 0
            if "HUMAN_BUSY" in problem.policy.objective.stages:
                seed = pool.best(cap)
                if seed and seed.candidate.metrics:
                    quality_spread_cap = max(
                        0,
                        objective_spread(seed.candidate.metrics, problem)
                        - problem.policy.objective.spread_target_sec,
                    )
            result = solve(
                ObjectiveStage(
                    name="E_QUALITY", makespan_cap_sec=cap, spread_excess_cap_sec=quality_spread_cap
                ),
                solver_end,
            )
            strict_accepted = (
                result is not None
                and result.candidate is not None
                and result.candidate.model_copy(update={"metrics": None}).candidate_hash
                in accepted_plans
            )
            if not strict_accepted:
                result = solve(ObjectiveStage(name="E_QUALITY", makespan_cap_sec=cap), solver_end)
            quality_accepted = (
                result is not None
                and result.candidate is not None
                and result.candidate.model_copy(update={"metrics": None}).candidate_hash
                in accepted_plans
            )
            total_human_optimized = (
                quality_accepted and "TOTAL_HUMAN_WORK" in problem.policy.objective.stages
            )
            human_optimized = quality_accepted and "HUMAN_BUSY" in problem.policy.objective.stages
        # 尚无合法候选时先把剩余求解预算用于建立完整可行解。
        first_end = (
            current + max(0, solver_end - current) * 40 // 100 if pool.values else solver_end
        )
        if not quality_first:
            solve(ObjectiveStage(name="A_MAKESPAN"), first_end)
        best = pool.best(makespan_first=True)
        if not quality_first and best is not None and best.candidate.metrics is not None:
            cap = makespan_cap(
                best.candidate.metrics.makespan_sec,
                problem.policy,
                serial.candidate.metrics.makespan_sec
                if serial and serial.candidate.metrics
                else None,
            )
            current = time.monotonic_ns()
            if "SPREAD" in problem.policy.objective.stages:
                solve(
                    ObjectiveStage(name="B_SPREAD", makespan_cap_sec=cap),
                    current + max(0, solver_end - current) // 2,
                )
            best = pool.best(cap)
            assert best is not None and best.candidate.metrics is not None
            excess = max(
                0,
                objective_spread(best.candidate.metrics, problem)
                - problem.policy.objective.spread_target_sec,
            )
            current = time.monotonic_ns()
            solve(
                ObjectiveStage(
                    name="C_MAKESPAN", makespan_cap_sec=cap, spread_excess_cap_sec=excess
                ),
                current + max(0, solver_end - current) * 2 // 3,
            )
            best = pool.best(cap)
            assert best is not None and best.candidate.metrics is not None
            human_count = human_phase_count(problem)
            stability_enabled = (
                self.previous_plan is not None and "STABILITY" in problem.policy.objective.stages
            )
            excess = max(
                0,
                objective_spread(best.candidate.metrics, problem)
                - problem.policy.objective.spread_target_sec,
            )
            total_enabled = "TOTAL_HUMAN_WORK" in problem.policy.objective.stages
            if total_enabled and time.monotonic_ns() < solver_end:
                current = time.monotonic_ns()
                result = solve(
                    ObjectiveStage(
                        name="D_TOTAL_HUMAN",
                        makespan_cap_sec=best.candidate.metrics.makespan_sec,
                        spread_excess_cap_sec=excess,
                    ),
                    current + max(0, solver_end - current) // 3,
                )
                total_human_optimized = (
                    result is not None
                    and result.candidate is not None
                    and result.candidate.candidate_hash in accepted
                )
                best = pool.best(cap)
                assert best is not None and best.candidate.metrics is not None
            assert best.candidate.metrics is not None
            if (
                "HUMAN_BUSY" in problem.policy.objective.stages
                and human_count <= problem.policy.max_exact_human_phases
                and solver_end - time.monotonic_ns()
                >= problem.policy.minimum_exact_human_budget_ms * 1_000_000
                and not problem.model_soft_limit_exceedances
            ):
                result = solve(
                    ObjectiveStage(
                        name="D_HUMAN",
                        makespan_cap_sec=best.candidate.metrics.makespan_sec,
                        spread_excess_cap_sec=excess,
                        total_human_cap_sec=best.candidate.metrics.total_human_work_sec
                        if total_enabled
                        else None,
                    ),
                    (time.monotonic_ns() + max(0, solver_end - time.monotonic_ns()) // 2)
                    if stability_enabled
                    else solver_end,
                )
                human_optimized = (
                    result is not None
                    and result.candidate is not None
                    and result.candidate.candidate_hash in accepted
                )
            if (
                stability_enabled
                and human_count <= problem.policy.max_exact_human_phases
                and solver_end - time.monotonic_ns()
                >= problem.policy.minimum_exact_human_budget_ms * 1_000_000
                and not problem.model_soft_limit_exceedances
            ):
                best = pool.best(cap)
                assert best is not None and best.candidate.metrics is not None
                result = solve(
                    ObjectiveStage(
                        name="D_STABILITY",
                        makespan_cap_sec=best.candidate.metrics.makespan_sec,
                        spread_excess_cap_sec=max(
                            0,
                            objective_spread(best.candidate.metrics, problem)
                            - problem.policy.objective.spread_target_sec,
                        ),
                        human_busy_cap_sec=best.candidate.metrics.max_continuous_human_sec,
                        total_human_cap_sec=best.candidate.metrics.total_human_work_sec
                        if total_enabled
                        else None,
                        previous_plan=self.previous_plan,
                    ),
                    solver_end,
                )
                stability_optimized = (
                    result is not None
                    and result.candidate is not None
                    and result.candidate.candidate_hash in accepted
                )
        best = pool.best(cap)
        tail_timings: tuple[PhaseTiming, ...] = ()
        if best is not None and problem.policy.objective.spread_basis == "COOKING_FINISH":
            tail_started = time.monotonic_ns()
            tail_end = min(
                tail_started + 100_000_000,
                compute_end - max(50_000_000, pool.max_validation_elapsed_ns * 2),
            )
            compacted = compact_cooking_tails(
                best.candidate,
                problem,
                Deadline(expires_at_ns=tail_end),
                self.previous_plan,
            )
            if compacted != best.candidate and time.monotonic_ns() < compute_end:
                from_solver = (
                    best.candidate.model_copy(update={"metrics": None}).candidate_hash
                    in accepted_plans
                )
                if accept_candidate(compacted):
                    # 原求解阶段的候选、目标值和最优界保留；压紧另记计时，
                    # 最终计划重新校验，不将它冒充原 Solver 的最优性证据。
                    if from_solver:
                        accepted_plans.add(
                            compacted.model_copy(update={"metrics": None}).candidate_hash
                        )
                    best = pool.best(cap)
            tail_timings = (
                PhaseTiming(
                    stage="TAIL_COMPACTION",
                    elapsed_ms=(time.monotonic_ns() - tail_started) // 1_000_000,
                ),
            )
        heating_timings: tuple[PhaseTiming, ...] = ()
        if best is not None and problem.policy.objective.spread_basis == "COOKING_FINISH":
            heating_started = time.monotonic_ns()
            tightened = compact_heating_slack(
                best.candidate,
                problem,
                Deadline(
                    expires_at_ns=min(
                        heating_started + 200_000_000,
                        compute_end - max(50_000_000, pool.max_validation_elapsed_ns * 2),
                    )
                ),
                self.previous_plan,
            )
            if tightened != best.candidate and time.monotonic_ns() < compute_end:
                from_solver = (
                    best.candidate.model_copy(update={"metrics": None}).candidate_hash
                    in accepted_plans
                )
                if accept_candidate(tightened):
                    best = next(
                        v
                        for v in pool.values.values()
                        if v.candidate.assignments == tightened.assignments
                    )
                    if from_solver:
                        accepted_plans.add(
                            best.candidate.model_copy(update={"metrics": None}).candidate_hash
                        )
            heating_timings = (
                PhaseTiming(
                    stage="HEATING_SLACK_COMPACTION",
                    elapsed_ms=(time.monotonic_ns() - heating_started) // 1_000_000,
                ),
            )
        timing = (
            *greedy.timings,
            *(timing for phase in phases for timing in phase.timings),
            *reverse_timings,
            *tail_timings,
            *heating_timings,
            PhaseTiming(
                stage="VALIDATION_AND_METRICS", elapsed_ms=pool.validation_elapsed_ns // 1_000_000
            ),
            PhaseTiming(
                stage="PLANNING_ENGINE", elapsed_ms=(time.monotonic_ns() - started) // 1_000_000
            ),
        )
        if best is None:
            last = phases[-1] if phases else None
            failure_class = (
                "MODEL_INVALID"
                if last and last.status == "MODEL_INVALID"
                else "INFEASIBLE_MODEL"
                if last and last.status == "INFEASIBLE"
                else "NO_SOLUTION_WITHIN_BUDGET"
            )
            return PlanningResult(
                status="FAILED",
                failure=PlanningFailure(
                    code="NO_FEASIBLE_PLAN",
                    failure_class=failure_class,
                    message="没有通过独立校验的完整候选",
                ),
                stage_results=tuple(phases),
                timings=timing,
            )
        if (fast_replan or quality_first) and serial is None:
            return PlanningResult(
                status="FAILED",
                failure=PlanningFailure(
                    code="STATE_INCOMPLETE",
                    failure_class="STATE_INCOMPLETE",
                    message="排程缺少当前状态的已验证串行参考",
                ),
                stage_results=tuple(phases),
                rejected_candidates=tuple(pool.rejections),
                timings=timing,
            )
        return PlanningResult(
            status="VALIDATED",
            first_validated_candidate_ms=first_validated_ms,
            selected_candidate_source=(
                "CP_SAT"
                if best.candidate.model_copy(update={"metrics": None}).candidate_hash
                in accepted_plans
                else "GREEDY"
            ),
            solver_fallback_used=not bool(accepted_plans),
            candidate=best.candidate,
            validation=best.validation,
            solve_result=phases[-1] if phases else None,
            stage_results=tuple(phases),
            human_objective_optimized=human_optimized,
            total_human_objective_optimized=total_human_optimized,
            stability_objective_optimized=stability_optimized,
            makespan_cap_sec=cap,
            serial_reference_candidate=serial.candidate if serial else None,
            serial_reference_validation=serial.validation if serial else None,
            rejected_candidates=tuple(pool.rejections),
            timings=timing,
        )
