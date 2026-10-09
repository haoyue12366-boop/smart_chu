"""三个有限确定性构造共享一个截止时间，失败不宣称数学不可行。"""

import time

from app.domain.carrier_timing import task_intervals
from app.domain.ids import RecipeInstanceId, TaskId
from app.domain.ports import Deadline
from app.domain.reports import GreedyResult, PhaseTiming
from app.domain.schedule import CandidateSchedule, RecipeCompletion, ScheduledAssignment
from app.domain.scheduling_problem import CandidateCarrier, SchedulingProblem, TaskDependency
from app.domain.serial_order import serial_task_groups
from app.domain.time import Interval
from app.scheduling.calendar_projection import fixed_ports
from app.scheduling.calendar_types import PlacementResult
from app.scheduling.calendars import CalendarState
from app.scheduling.layer_resources import allocate_layers
from app.scheduling.layouts import recipe_layout
from app.scheduling.metrics import compute_metrics
from app.scheduling.objectives import makespan_cap
from app.scheduling.placement import find_layout_placement
from app.scheduling.ranking import CandidateRank, candidate_rank


class GreedyScheduler:
    def solve_extension(
        self, problem: SchedulingProblem, previous: CandidateSchedule, deadline: Deadline
    ) -> GreedyResult:
        """加菜时先扩展旧完整并行方案；只作候选，不冻结未来工序。"""
        started = time.monotonic_ns()
        try:
            state = CalendarState(problem)
            carriers = {c.carrier_id for c in problem.standalone_candidates}
            carriers.update(c.carrier_id for c in problem.shared_prep_candidates)
            carriers.update(c.carrier_id for c in problem.thermal_batch_candidates)
            carriers.update(c.carrier_id for c in problem.inventory_supply_candidates)
            tasks = {t.task_id for t in problem.logical_tasks}
            known = set(state.current.covered)
            retained = tuple(
                a
                for a in previous.assignments
                if a.carrier_id in carriers
                and set(a.task_ids) <= tasks
                and not known.intersection(a.task_ids)
            )
            if retained:
                placement = find_layout_placement(retained, state, problem, deadline)
                if not placement.assignments:
                    raise ValueError(str(placement.rejection_reasons))
                state.commit(placement)
            for instance in problem.recipe_instances:
                if time.monotonic_ns() >= deadline.expires_at_ns:
                    raise TimeoutError("加菜候选构造共享截止时间已到")
                required = {
                    t.task_id
                    for t in problem.logical_tasks
                    if t.recipe_instance_id == instance.recipe_instance_id
                }
                if required <= set(state.current.covered):
                    continue
                layout = recipe_layout(problem, instance.recipe_instance_id, 0, deadline)
                layout = tuple(
                    a for a in layout if not set(a.task_ids).intersection(state.current.covered)
                )
                placement = find_layout_placement(layout, state, problem, deadline)
                if not placement.assignments:
                    raise ValueError(str(placement.rejection_reasons))
                state.commit(placement)
            candidate = _complete_layout(problem, state.current.assignments)
            return GreedyResult(
                status="CANDIDATE_FOUND",
                candidate=candidate,
                completed_variants=1,
                timings=(
                    PhaseTiming(
                        stage="PREVIOUS_PLAN_EXTENSION",
                        elapsed_ms=(time.monotonic_ns() - started) // 1_000_000,
                    ),
                ),
            )
        except (ValueError, TimeoutError) as exc:
            return GreedyResult(
                status="BUDGET_EXHAUSTED"
                if isinstance(exc, TimeoutError)
                else "CONSTRUCTION_FAILED",
                reason=str(exc),
            )

    def solve_serial(self, problem: SchedulingProblem, deadline: Deadline) -> GreedyResult:
        """有限逐道构造，作为可校验的参考候选；不宣称串行最优或不可行。"""
        started = time.monotonic_ns()
        candidate = None
        reason = None
        timed_out = False
        try:
            if (
                problem.fixed_executions
                or problem.fixed_supply_fulfillments
                or problem.advance_preparations
            ):
                candidate = self._serial_remaining(problem, deadline)
                return GreedyResult(
                    status="CANDIDATE_FOUND",
                    candidate=candidate,
                    timings=(
                        PhaseTiming(
                            stage="GREEDY", elapsed_ms=(time.monotonic_ns() - started) // 1_000_000
                        ),
                    ),
                    completed_variants=1,
                )
            state = CalendarState(problem)
            previous_end = problem.runtime.now_offset_sec
            for instance in problem.recipe_instances:
                if time.monotonic_ns() >= deadline.expires_at_ns:
                    raise TimeoutError("逐道构造共享截止时间已到")
                layout = recipe_layout(problem, instance.recipe_instance_id, 0, deadline)
                if not layout:
                    continue
                first = min(a.interval.start_sec for a in layout)
                shift = max(0, previous_end - first)
                layout = tuple(
                    a.model_copy(
                        update={
                            "interval": Interval(
                                start_sec=a.interval.start_sec + shift,
                                end_sec=a.interval.end_sec + shift,
                            )
                        }
                    )
                    for a in layout
                )
                placement = find_layout_placement(layout, state, problem, deadline)
                if not placement.assignments:
                    raise ValueError(str(placement.rejection_reasons))
                state.commit(placement)
                previous_end = max(a.interval.end_sec for a in placement.assignments)
            candidate = _complete_layout(problem, state.current.assignments)
        except TimeoutError as exc:
            timed_out, reason = True, str(exc)
        except ValueError as exc:
            reason = str(exc)
        return GreedyResult(
            status="CANDIDATE_FOUND"
            if candidate
            else "BUDGET_EXHAUSTED"
            if timed_out
            else "CONSTRUCTION_FAILED",
            candidate=candidate,
            reason=reason,
            timings=(
                PhaseTiming(
                    stage="GREEDY", elapsed_ms=(time.monotonic_ns() - started) // 1_000_000
                ),
            ),
            completed_variants=int(candidate is not None),
        )

    def _serial_remaining(
        self, problem: SchedulingProblem, deadline: Deadline
    ) -> CandidateSchedule:
        groups = serial_task_groups(problem)
        edges: list[TaskDependency] = []
        for left, right in zip(groups, groups[1:], strict=False):
            left_set, right_set = set(left), set(right)
            outgoing = {
                d.predecessor_id
                for d in problem.dependencies
                if d.predecessor_id in left_set and d.successor_id in left_set
            }
            incoming = {
                d.successor_id
                for d in problem.dependencies
                if d.predecessor_id in right_set and d.successor_id in right_set
            }
            edges.extend(
                TaskDependency(
                    predecessor_id=before,
                    successor_id=after,
                    evidence_refs=("serial-reference:remaining-menu",),
                )
                for before in left_set - outgoing
                for after in right_set - incoming
            )
        sequenced = problem.model_copy(update={"dependencies": (*problem.dependencies, *edges)})
        layout = recipe_layout(sequenced, None, 0, deadline)
        placement = find_layout_placement(layout, CalendarState(sequenced), sequenced, deadline)
        if not placement.assignments:
            raise ValueError(str(placement.rejection_reasons))
        return _complete_layout(problem, placement.assignments)

    def solve(self, problem: SchedulingProblem, deadline: Deadline) -> GreedyResult:
        started = time.monotonic_ns()
        best: CandidateSchedule | None = None
        best_key: CandidateRank | None = None
        complete: list[CandidateSchedule] = []
        trace: list[str] = []
        rollback_count = 0
        completed_variants = 0
        timed_out = False
        limits = problem.policy.greedy_search
        # 跨菜冻结批次的收尾共享人工，先构造全菜单布局，允许各菜未来段分别移动。
        if problem.fixed_executions:
            try:
                proposal = recipe_layout(problem, None, 0, deadline)
                if proposal:
                    placement = find_layout_placement(
                        proposal, CalendarState(problem), problem, deadline
                    )
                    if not placement.assignments:
                        raise ValueError(str(placement.rejection_reasons))
                    proposal = placement.assignments
                best = _complete_layout(problem, proposal)
                complete.append(best)
                best_key = candidate_rank(best, problem, makespan_first=True)
                completed_variants += 1
                trace.append("fixed-global-layout")
            except TimeoutError as exc:
                timed_out = True
                trace.append(str(exc))
            except ValueError as exc:
                trace.append("fixed-global-layout-rejected:" + str(exc))
        # P2 无自由跨菜共享。以整道菜的有限内部布局作为事务单位，保持固定程序完整。
        try:
            for variant in range(
                min(3, limits.max_variants) + min(1, limits.max_standalone_restarts)
            ):
                if variant >= min(3, limits.max_variants) and best is not None:
                    break
                if time.monotonic_ns() >= deadline.expires_at_ns:
                    raise TimeoutError("Greedy 共享截止时间已到")
                state = CalendarState(problem)
                layouts: dict[RecipeInstanceId, tuple[ScheduledAssignment, ...]] = {}
                failed_layout = False
                for recipe_instance in problem.recipe_instances:
                    try:
                        layout = recipe_layout(
                            problem, recipe_instance.recipe_instance_id, variant % 3, deadline
                        )
                    except ValueError as exc:
                        trace.append(f"variant={variant}:layout-failed:{exc}")
                        failed_layout = True
                        break
                    if layout:
                        layouts[recipe_instance.recipe_instance_id] = layout
                if failed_layout:
                    continue
                remaining = set(layouts)
                decisions: list[tuple[RecipeInstanceId, str]] = []
                excluded: set[tuple[str, str]] = set()
                attempts = 0
                rewind_depth = 0
                while remaining:
                    before = state.state_hash
                    placements: list[tuple[RecipeInstanceId, PlacementResult]] = []
                    evaluations = 0
                    for instance in sorted(remaining, key=lambda i: i.root):
                        if evaluations >= limits.max_placements_per_iteration:
                            break
                        if (before, instance.root) in excluded:
                            continue
                        evaluations += 1
                        placement = find_layout_placement(
                            layouts[instance], state, problem, deadline
                        )
                        if placement.assignments:
                            placements.append((instance, placement))
                    if not placements:
                        if (
                            decisions
                            and attempts < limits.rollback_attempts
                            and rewind_depth < limits.rollback_depth
                        ):
                            undone, previous_hash = decisions.pop()
                            state.rollback()
                            remaining.add(undone)
                            excluded.add((previous_hash, undone.root))
                            attempts += 1
                            rewind_depth += 1
                            rollback_count += 1
                            trace.append(f"variant={variant}:rollback:{undone.root}")
                            continue
                        trace.append(f"variant={variant}:construction-failed")
                        break

                    def score(
                        item: tuple[RecipeInstanceId, PlacementResult],
                        layouts: dict[RecipeInstanceId, tuple[ScheduledAssignment, ...]] = layouts,
                        variant: int = variant,
                    ) -> tuple[int, int, str]:
                        instance, placement = item
                        assert isinstance(placement, PlacementResult)
                        finish = max(a.interval.end_sec for a in placement.assignments)
                        duration = max(a.interval.end_sec for a in layouts[instance])
                        window = (
                            min(
                                (t.latest_end_sec or problem.horizon_sec)
                                for t in problem.logical_tasks
                                if t.recipe_instance_id == instance
                            )
                            - finish
                        )
                        return (
                            (
                                -duration
                                if variant % 3 == 0
                                else window
                                if variant % 3 == 1
                                else finish
                            ),
                            finish,
                            instance.root,
                        )

                    instance, chosen = min(placements, key=score)
                    state.commit(chosen)
                    rewind_depth = 0
                    decisions.append((instance, before))
                    remaining.remove(instance)
                    trace.append(
                        f"variant={variant}:place:{instance.root}:gap_checks={chosen.gap_checks}"
                    )
                if remaining:
                    continue
                ports = {p.task_id: p.interval for p in state.current.ports}
                if set(ports) != {t.task_id for t in problem.logical_tasks}:
                    continue
                completions = tuple(
                    RecipeCompletion(
                        recipe_instance_id=instance.recipe_instance_id,
                        completion_sec=max(
                            ports[t.task_id].end_sec
                            for t in problem.logical_tasks
                            if t.recipe_instance_id == instance.recipe_instance_id
                        ),
                    )
                    for instance in problem.recipe_instances
                )
                candidate = CandidateSchedule(
                    problem_hash=problem.problem_hash,
                    assignments=state.current.assignments,
                    recipe_completions=completions,
                )
                candidate = allocate_layers(candidate, problem)
                candidate = candidate.model_copy(
                    update={"metrics": compute_metrics(candidate, problem)}
                )
                complete.append(candidate)
                key = candidate_rank(candidate, problem, makespan_first=True)
                if best_key is None or key < best_key:
                    best, best_key = candidate, key
                completed_variants += 1
        except TimeoutError as exc:
            timed_out = True
            trace.append(str(exc))
        except ValueError as exc:
            trace.append(str(exc))
        # Global layouts can synchronize different recipes. Every alternative shares the
        # original deadline and competes against the independently retained standalone plan.
        optional_groups = (
            *problem.shared_prep_candidates,
            *problem.thermal_batch_candidates,
            *problem.inventory_supply_candidates,
        )
        if (
            optional_groups or problem.fixed_executions
        ) and time.monotonic_ns() < deadline.expires_at_ns:
            groups: list[tuple[CandidateCarrier, ...]] = [
                *(((),) if problem.fixed_executions and best is None else ()),
                *((c,) for c in optional_groups),
            ]
            packed = []
            covered: set[TaskId] = set()
            for carrier in optional_groups:
                if not covered.intersection(carrier.covers):
                    packed.append(carrier)
                    covered.update(carrier.covers)
            if len(packed) > 1:
                groups.insert(0, tuple(packed))
            for group in groups[: limits.max_placements_per_iteration]:
                try:
                    proposal = recipe_layout(problem, None, 0, deadline, group)
                    placement = find_layout_placement(
                        proposal, CalendarState(problem), problem, deadline
                    )
                    if not placement.assignments:
                        trace.append("shared-layout-rejected:" + str(placement.rejection_reasons))
                        continue
                    proposal = placement.assignments
                    candidate = _complete_layout(problem, proposal)
                    complete.append(candidate)
                    key = candidate_rank(candidate, problem, makespan_first=True)
                    if best_key is None or key < best_key:
                        best, best_key = candidate, key
                    completed_variants += 1
                    trace.append("shared-layout:" + ",".join(c.carrier_id.root for c in group))
                except TimeoutError as exc:
                    timed_out = True
                    trace.append(str(exc))
                    break
                except ValueError as exc:
                    trace.append("shared-layout-rejected:" + str(exc))
        if best is not None and best.metrics is not None:
            cap = makespan_cap(best.metrics.makespan_sec, problem.policy)
            best = min(
                (c for c in complete if c.metrics is not None and c.metrics.makespan_sec <= cap),
                key=lambda c: candidate_rank(c, problem),
            )
        timings = (
            PhaseTiming(stage="GREEDY", elapsed_ms=(time.monotonic_ns() - started) // 1_000_000),
        )
        return GreedyResult(
            status="CANDIDATE_FOUND"
            if best is not None
            else "BUDGET_EXHAUSTED"
            if timed_out
            else "CONSTRUCTION_FAILED",
            candidate=best,
            reason=None if best else (trace[-1] if trace else "没有完整合法布局"),
            timings=timings,
            decision_trace=tuple(trace),
            completed_variants=completed_variants,
            rollback_count=rollback_count,
        )


def _complete_layout(
    problem: SchedulingProblem, proposal: tuple[ScheduledAssignment, ...]
) -> CandidateSchedule:
    ports = task_intervals(problem, proposal)
    ports.update((p.task_id, p.interval) for p in fixed_ports(problem))
    if set(ports) != {task.task_id for task in problem.logical_tasks}:
        raise ValueError("布局尚未覆盖全部剩余需求与冻结事实")
    candidate = CandidateSchedule(
        problem_hash=problem.problem_hash,
        assignments=proposal,
        recipe_completions=tuple(
            RecipeCompletion(
                recipe_instance_id=instance.recipe_instance_id,
                completion_sec=max(
                    ports[task.task_id].end_sec
                    for task in problem.logical_tasks
                    if task.recipe_instance_id == instance.recipe_instance_id
                ),
            )
            for instance in problem.recipe_instances
        ),
    )
    candidate = allocate_layers(candidate, problem)
    return candidate.model_copy(update={"metrics": compute_metrics(candidate, problem)})
