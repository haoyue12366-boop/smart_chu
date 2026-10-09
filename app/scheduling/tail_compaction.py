"""在共享截止时间内提前出锅后的收尾；不改变出锅锚点、设备选择或实际事实。"""

import time
from dataclasses import replace

from app.domain.carrier_timing import task_intervals
from app.domain.ids import TaskId
from app.domain.ports import Deadline
from app.domain.schedule import CandidateSchedule, RecipeCompletion, ScheduledAssignment
from app.domain.scheduling_problem import SchedulingProblem
from app.domain.time import Interval
from app.scheduling.calendar_projection import entries_for
from app.scheduling.calendar_types import TaskPort
from app.scheduling.calendars import CalendarState
from app.scheduling.metrics import compute_metrics
from app.scheduling.placement import find_layout_placement
from app.scheduling.ranking import candidate_rank


def compact_cooking_tails(
    candidate: CandidateSchedule,
    problem: SchedulingProblem,
    deadline: Deadline,
    previous: CandidateSchedule | None = None,
) -> CandidateSchedule:
    """有限局部改进；调用方必须独立校验新候选，失败时保留原完整计划。"""
    if (
        problem.policy.objective.spread_basis != "COOKING_FINISH"
        or time.monotonic_ns() >= deadline.expires_at_ns
    ):
        return candidate
    tails = _tail_tasks(problem)
    standalone = {c.carrier_id for c in problem.standalone_candidates}
    movable = sorted(
        (
            a
            for a in candidate.assignments
            if a.carrier_id in standalone
            and len(a.task_ids) == 1
            and a.task_ids[0] in tails
            and all(use.resource_type == "HUMAN" for use in a.resource_uses)
        ),
        key=lambda a: (-a.interval.end_sec, a.carrier_id.root),
    )
    current = candidate
    for assignment in movable:
        if time.monotonic_ns() >= deadline.expires_at_ns:
            break
        others = tuple(a for a in current.assignments if a.carrier_id != assignment.carrier_id)
        calendars = CalendarState(problem)
        ports = task_intervals(problem, others)
        calendars.current = replace(
            calendars.current,
            assignments=others,
            entries=calendars.current.entries + entries_for(problem, others),
            ports=calendars.current.ports
            + tuple(TaskPort(task_id=task, interval=span) for task, span in ports.items()),
        )
        layout = (
            assignment.model_copy(
                update={
                    "interval": Interval(
                        start_sec=0,
                        end_sec=assignment.interval.end_sec - assignment.interval.start_sec,
                    )
                }
            ),
        )
        # 先试真实最早空档；若会延长连续人工，再保留完整休息间隔重试一次。
        for preserve_rest in (False, True):
            if preserve_rest:
                if not assignment.resource_uses:
                    break
                gap = problem.policy.objective.rest_gap_sec
                calendars.current = replace(
                    calendars.current,
                    entries=tuple(
                        replace(
                            entry,
                            interval=Interval(
                                start_sec=max(0, entry.interval.start_sec - gap),
                                end_sec=entry.interval.end_sec + gap,
                            ),
                        )
                        if entry.use.resource_type == "HUMAN"
                        else entry
                        for entry in calendars.current.entries
                    ),
                )
            try:
                placement = find_layout_placement(
                    layout, calendars, problem, deadline, lock_frozen_anchor=False
                )
            except TimeoutError:
                return current
            if not placement.assignments:
                break
            moved = placement.assignments[0]
            if moved.interval.start_sec >= assignment.interval.start_sec:
                break
            proposal = _replace(current, assignment, moved, problem)
            before = current.metrics or compute_metrics(current, problem)
            after = proposal.metrics
            assert after is not None
            if (
                after.recipe_cooking_finishes == before.recipe_cooking_finishes
                and after.max_continuous_human_sec <= before.max_continuous_human_sec
                and after.remaining_max_continuous_human_sec
                <= before.remaining_max_continuous_human_sec
                and candidate_rank(proposal, problem, previous)[:-1]
                <= candidate_rank(current, problem, previous)[:-1]
            ):
                current = proposal
                break
    return current


def _tail_tasks(problem: SchedulingProblem) -> set[TaskId]:
    anchors = {task for boundary in problem.cooking_completions for task in boundary.task_ids}
    successors: dict[TaskId, list[TaskId]] = {}
    for dependency in problem.dependencies:
        successors.setdefault(dependency.predecessor_id, []).append(dependency.successor_id)
    seen = set(anchors)
    pending = list(anchors)
    while pending:
        for task in successors.get(pending.pop(), ()):
            if task not in seen:
                seen.add(task)
                pending.append(task)
    return seen - anchors


def _replace(
    candidate: CandidateSchedule,
    original: ScheduledAssignment,
    moved: ScheduledAssignment,
    problem: SchedulingProblem,
) -> CandidateSchedule:
    proposal = candidate.model_copy(
        update={
            "assignments": tuple(
                moved if a.carrier_id == original.carrier_id else a for a in candidate.assignments
            ),
            "metrics": None,
            "recipe_completions": (),
        }
    )
    if candidate.recipe_completions:
        ports = {p.task_id: p.interval for p in CalendarState(problem).current.ports}
        ports.update(task_intervals(problem, proposal.assignments))
        proposal = proposal.model_copy(
            update={
                "recipe_completions": tuple(
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
            }
        )
    return proposal.model_copy(update={"metrics": compute_metrics(proposal, problem)})
