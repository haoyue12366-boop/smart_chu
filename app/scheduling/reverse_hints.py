"""以当前最晚出锅为界，整体后移完整预约及关联后续，生成有界搜索提示。"""

import time
from collections import defaultdict
from dataclasses import replace

from app.domain.carrier_timing import task_intervals
from app.domain.ids import TaskId
from app.domain.ports import Deadline
from app.domain.schedule import CandidateSchedule
from app.domain.scheduling_problem import SchedulingProblem
from app.scheduling.calendar_projection import entries_for, fixed_ports
from app.scheduling.calendar_types import TaskPort
from app.scheduling.calendars import CalendarState
from app.scheduling.metrics import compute_metrics
from app.scheduling.placement import find_layout_placement
from app.scheduling.ranking import candidate_rank


def align_cooking_finishes(
    candidate: CandidateSchedule,
    problem: SchedulingProblem,
    deadline: Deadline,
    previous: CandidateSchedule | None = None,
) -> CandidateSchedule:
    """保留时长、完整预约及历史；调用方独立校验后才可采纳或作为求解提示。"""
    if (
        problem.policy.objective.spread_basis != "COOKING_FINISH"
        or time.monotonic_ns() >= deadline.expires_at_ns
    ):
        return candidate
    metrics = candidate.metrics or compute_metrics(candidate, problem)
    target = max((f.cooking_finish_sec for f in metrics.recipe_cooking_finishes), default=0)
    fixed = {p.task_id for p in fixed_ports(problem)}
    links = _linked_successors(candidate, problem)
    boundaries = {b.recipe_instance_id: b for b in problem.cooking_completions}
    current = best = candidate
    for finish in sorted(metrics.recipe_cooking_finishes, key=lambda f: f.cooking_finish_sec):
        if time.monotonic_ns() >= deadline.expires_at_ns:
            break
        block = set(boundaries[finish.recipe_instance_id].task_ids)
        pending = list(block)
        while pending:
            for task in links[pending.pop()]:
                if task not in block:
                    block.add(task)
                    pending.append(task)
        if block & fixed:
            continue
        layout = tuple(a for a in current.assignments if set(a.task_ids) & block)
        if not layout:
            continue
        ports = task_intervals(problem, current.assignments)
        cooking_end = max(
            ports[task].end_sec
            for boundary in problem.cooking_completions
            for task in boundary.task_ids
            if task in block
        )
        latest_shift = min(
            target - cooking_end,
            metrics.makespan_sec - max(a.interval.end_sec for a in layout),
        )
        if latest_shift <= 0:
            continue
        others = tuple(a for a in current.assignments if not set(a.task_ids) & block)
        calendars = CalendarState(problem)
        calendars.current = replace(
            calendars.current,
            assignments=others,
            entries=calendars.current.entries + entries_for(problem, others),
            ports=calendars.current.ports
            + tuple(
                TaskPort(task_id=t, interval=span) for t, span in ports.items() if t not in block
            ),
        )
        try:
            placement = find_layout_placement(
                layout,
                calendars,
                problem,
                deadline,
                lock_frozen_anchor=False,
                latest_anchor_sec=latest_shift,
            )
        except TimeoutError:
            break
        if not placement.assignments:
            continue
        replacements = {a.carrier_id: a for a in placement.assignments}
        proposal = current.model_copy(
            update={
                "assignments": tuple(
                    replacements.get(a.carrier_id, a) for a in current.assignments
                ),
                "recipe_completions": (),
                "metrics": None,
            }
        )
        proposal = proposal.model_copy(update={"metrics": compute_metrics(proposal, problem)})
        # 多道菜同为最早出锅时，单次移动可能暂时不改变极差。
        # 保留这条有限探索路径，但只返回整体排序不劣的完整候选。
        current = proposal
        if (
            candidate_rank(proposal, problem, previous)[:-1]
            <= candidate_rank(best, problem, previous)[:-1]
        ):
            best = proposal
    return best


def _linked_successors(
    candidate: CandidateSchedule, problem: SchedulingProblem
) -> dict[TaskId, set[TaskId]]:
    links: dict[TaskId, set[TaskId]] = defaultdict(set)
    groups = [set(a.task_ids) for a in candidate.assignments]
    groups.extend(set(r.members) for r in problem.mandatory_programs.reservations)
    groups.extend({r.left_task, r.right_task} for r in problem.mandatory_programs.time_relations)
    for group in groups:
        for task in group:
            links[task].update(group - {task})
    for dependency in problem.dependencies:
        links[dependency.predecessor_id].add(dependency.successor_id)
        if dependency.max_lag_sec is not None:
            links[dependency.successor_id].add(dependency.predecessor_id)
    supplies = {s.supply_id: s for s in problem.material_flow.supplies}
    for demand in problem.material_flow.demands:
        producer = supplies[demand.supply_id].producer_task_id
        if producer is not None:
            links[producer].add(demand.task_id)
    return links
