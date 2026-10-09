"""CP-SAT 限时求解；UNKNOWN 与 INFEASIBLE 分开，提示不作为硬约束。"""

import math
import time
from collections import defaultdict
from copy import copy

from app.domain.carrier_timing import member_offsets
from app.domain.objectives import ObjectiveStage
from app.domain.ports import Deadline
from app.domain.reports import PhaseTiming, SolverBuildReport, SolveResult, SolveStatus
from app.domain.schedule import CandidateSchedule
from app.domain.scheduling_problem import SchedulingProblem
from app.scheduling.build_report import make_build_report
from app.scheduling.metrics import objective_spread
from app.scheduling.model_builder import ModelBuilder
from app.scheduling.solution_mapping import map_solution


class CpSatScheduler:
    def __init__(self) -> None:
        self.last_build_report: SolverBuildReport | None = None
        self._base_builder: ModelBuilder | None = None

    def _builder(self, problem: SchedulingProblem, deadline: Deadline) -> ModelBuilder:
        """仅缓存一个完整身份匹配的基础模型；每轮独立克隆目标与附加约束。"""
        base = self._base_builder
        if base is None or base.problem.problem_hash != problem.problem_hash:
            base = ModelBuilder(problem, deadline)
            base.build()
            self._base_builder = base
        builder = copy(base)
        builder.deadline = deadline
        builder.model = base.model.clone()
        builder.constraint_indices = defaultdict(
            set, {k: set(v) for k, v in base.constraint_indices.items()}
        )
        builder.additional_mappings = list(base.additional_mappings)
        builder.human_intervals = list(base.human_intervals)
        return builder

    def solve(
        self,
        problem: SchedulingProblem,
        hint: CandidateSchedule | None,
        deadline: Deadline,
        *,
        serial_menu: bool = False,
        stage: ObjectiveStage | None = None,
    ) -> SolveResult:
        started = time.monotonic_ns()
        identity = problem.problem_hash
        self.last_build_report = None
        if started >= deadline.expires_at_ns:
            return SolveResult(
                status="UNKNOWN", problem_hash=identity, diagnostic_message="求解前共享预算已耗尽"
            )
        try:
            from ortools.sat.python import cp_model

            builder = self._builder(problem, deadline)
            if serial_menu:
                builder.add_serial_order()
            if stage is not None:
                if serial_menu:
                    raise ValueError("串行参考不能同时接受并行优化阶段")
                from app.scheduling.objectives import apply_stage

                apply_stage(builder, stage)
            if hint is not None and hint.problem_hash == identity:
                hinted = {a.carrier_id: a for a in hint.assignments}
                carriers = {c.carrier_id: c for c in builder.candidates}
                hinted_coverage = {task for a in hint.assignments for task in a.task_ids}
                hinted_tasks = set()
                hint_times = not (
                    stage is not None
                    and stage.spread_excess_cap_sec == 0
                    and hint.metrics is not None
                    and objective_spread(hint.metrics, problem)
                    > problem.policy.objective.spread_target_sec
                )
                for carrier_id, selected in builder.selected.items():
                    carrier = carriers[carrier_id]
                    if carrier_id in hinted or set(carrier.covers) <= hinted_coverage:
                        builder.model.add_hint(selected, int(carrier_id in hinted))
                    assignment = hinted.get(carrier_id)
                    # 合法资源/加工路径仍可提示；违背集中出菜界的时间不提示。
                    if assignment is not None and hint_times:
                        for task, (offset_start, offset_end) in member_offsets(carrier).items():
                            if task in builder.starts and task not in hinted_tasks:
                                builder.model.add_hint(
                                    builder.starts[task],
                                    assignment.interval.start_sec + offset_start,
                                )
                                builder.model.add_hint(
                                    builder.ends[task], assignment.interval.start_sec + offset_end
                                )
                                hinted_tasks.add(task)
                if (
                    builder.human_chain_enabled
                    and hint_times
                    and hinted_coverage | builder.fixed.keys() == builder.starts.keys()
                ):
                    from app.scheduling.human_hint import add_human_chain_hint

                    add_human_chain_hint(builder, hint)
            builder.check_budget()
            error = builder.model.validate()
            if error:
                return SolveResult(
                    status="MODEL_INVALID", problem_hash=identity, diagnostic_message=error
                )
            builder.check_budget()
            report = make_build_report(builder, (time.monotonic_ns() - started) // 1_000_000)
            self.last_build_report = report
            builder.check_budget()
            solver = cp_model.CpSolver()
            solver.parameters.max_time_in_seconds = (
                deadline.expires_at_ns - time.monotonic_ns()
            ) / 1e9
            if solver.parameters.max_time_in_seconds <= 0:
                raise TimeoutError("求解调用前预算已耗尽")
            solver.parameters.num_search_workers = problem.policy.max_solver_search_workers
            solver.parameters.random_seed = 42
            if builder.human_chain_enabled and problem.policy.max_solver_search_workers == 1:
                # 单线程的连续人工模型主要靠时间/布尔传播；避免可选探测及
                # 线性松弛抢占搜索时间。保留全部约束、精确目标和原预算，
                # 求解器仍只在证明最优或共享截止到达时结束。
                solver.parameters.cp_model_probing_level = 0
                solver.parameters.linearization_level = 0
            status = solver.solve(builder.model)
            name = solver.status_name(status)
            self.last_build_report = report.model_copy(update={"solve_status": SolveStatus(name)})
            timings = (
                PhaseTiming(
                    stage="CP_SAT", elapsed_ms=(time.monotonic_ns() - started) // 1_000_000
                ),
            )
            if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                # 从整数变量读取目标；浮点 best_bound 保守向下取界。
                value = solver.value(builder.objective_variable)
                bound = (
                    value
                    if status == cp_model.OPTIMAL
                    else max(0, math.floor(math.nextafter(solver.best_objective_bound, -math.inf)))
                )
                return SolveResult(
                    status=name,
                    problem_hash=identity,
                    candidate=map_solution(builder, solver),
                    objective_stage=builder.objective_stage,
                    objective_value=value,
                    best_bound=bound,
                    build_report_ref=report.solver_build_id,
                    timings=timings,
                )
            return SolveResult(
                status=name,
                problem_hash=identity,
                objective_stage=builder.objective_stage,
                build_report_ref=report.solver_build_id,
                timings=timings,
            )
        except TimeoutError as exc:
            return SolveResult(status="UNKNOWN", problem_hash=identity, diagnostic_message=str(exc))
        except (ValueError, TypeError, OverflowError) as exc:
            return SolveResult(
                status="MODEL_INVALID", problem_hash=identity, diagnostic_message=str(exc)
            )
