"""从选中任务与真实历史重新计算指标，不读取候选自报的完成时刻。"""

from app.domain.carrier_timing import task_intervals
from app.domain.cooking_completion import cooking_finish_times
from app.domain.ids import TaskId
from app.domain.runtime_history import unmodeled_human_history
from app.domain.schedule import (
    CandidateSchedule,
    RecipeCookingFinish,
    ScheduleDisruption,
    ScheduleMetrics,
)
from app.domain.scheduling_problem import SchedulingProblem
from app.domain.time import Interval
from app.scheduling.calendar_projection import fixed_ports


def compute_metrics(schedule: CandidateSchedule, problem: SchedulingProblem) -> ScheduleMetrics:
    if schedule.problem_hash != problem.problem_hash:
        raise ValueError("指标输入问题身份不一致")
    ports = {p.task_id: p.interval for p in fixed_ports(problem)}
    human: list[Interval] = [span.interval for span in unmodeled_human_history(problem)]
    task_map = {t.task_id: t for t in problem.logical_tasks}
    for execution in problem.fixed_executions:
        explicit = (
            execution.scheduled_resource_spans
            if execution.status == "RUNNING"
            else execution.resource_spans
        )
        if explicit:
            human.extend(p.interval for p in explicit if p.resource.resource_type == "HUMAN")
            continue
        if any(
            any(u.resource_type == "HUMAN" for u in task_map[t].operation.resource_requirements)
            for t in execution.task_ids
            if t in task_map
        ):
            intervals = {ports[t] for t in execution.task_ids if t in ports}
            human.extend(intervals)
    thermal = {c.carrier_id: c for c in problem.thermal_batch_candidates}
    ports.update(task_intervals(problem, schedule.assignments))
    for assignment in schedule.assignments:
        carrier = thermal.get(assignment.carrier_id)
        if carrier is not None:
            human.extend(
                Interval(
                    start_sec=assignment.interval.start_sec + p.start_offset_sec,
                    end_sec=assignment.interval.start_sec + p.end_offset_sec,
                )
                for p in carrier.resource_phases
                if p.resource_use.resource_type == "HUMAN"
            )
        if any(u.resource_type == "HUMAN" for u in assignment.resource_uses):
            human.append(assignment.interval)
    if set(ports) != set(task_map):
        raise ValueError("不完整计划没有全桌指标")
    completions = [
        max(
            ports[t.task_id].end_sec
            for t in problem.logical_tasks
            if t.recipe_instance_id == instance.recipe_instance_id
        )
        for instance in problem.recipe_instances
    ]
    now = problem.runtime.now_offset_sec
    cooking = cooking_finish_times(problem, ports)
    complete_cooking = set(cooking) == {
        i.recipe_instance_id for i in problem.recipe_instances
    } and (bool(cooking) or problem.policy.objective.spread_basis == "COOKING_FINISH")
    cooking_ends = list(cooking.values())
    future_cooking = [end for end in cooking_ends if end > now]
    future_completions = [end for end in completions if end > now]
    blocks: list[Interval] = []
    for interval in sorted(human, key=lambda i: (i.start_sec, i.end_sec)):
        if interval.start_sec == interval.end_sec:
            continue
        if (
            blocks
            and interval.start_sec - blocks[-1].end_sec < problem.policy.objective.rest_gap_sec
        ):
            blocks[-1] = Interval(
                start_sec=blocks[-1].start_sec, end_sec=max(blocks[-1].end_sec, interval.end_sec)
            )
        else:
            blocks.append(interval)
    return ScheduleMetrics(
        makespan_sec=max(completions, default=0),
        completion_spread_sec=max(completions) - min(completions) if completions else 0,
        cooking_finish_spread_sec=(max(cooking_ends, default=0) - min(cooking_ends, default=0))
        if complete_cooking
        else None,
        remaining_cooking_finish_spread_sec=(
            max(future_cooking, default=0) - min(future_cooking, default=0)
        )
        if complete_cooking
        else None,
        recipe_cooking_finishes=tuple(
            RecipeCookingFinish(recipe_instance_id=identity, cooking_finish_sec=end)
            for identity, end in cooking.items()
        ),
        max_continuous_human_sec=max((b.end_sec - b.start_sec for b in blocks), default=0),
        total_human_work_sec=sum(i.end_sec - i.start_sec for i in human),
        actual_human_work_sec=sum(max(0, min(now, i.end_sec) - i.start_sec) for i in human),
        remaining_human_work_sec=sum(max(0, i.end_sec - max(now, i.start_sec)) for i in human),
        remaining_makespan_sec=max(0, max(completions, default=0) - now),
        remaining_max_continuous_human_sec=max(
            (b.end_sec - b.start_sec for b in blocks if b.end_sec > now), default=0
        ),
        remaining_completion_spread_sec=(max(future_completions) - min(future_completions))
        if future_completions
        else 0,
        serial_reference_sec=problem.serial_reference.duration_sec
        if problem.serial_reference
        else None,
    )


def objective_spread(metrics: ScheduleMetrics, problem: SchedulingProblem) -> int:
    if problem.policy.objective.spread_basis == "COOKING_FINISH":
        if metrics.cooking_finish_spread_sec is None:
            raise ValueError("出锅目标缺少完整指标")
        return metrics.cooking_finish_spread_sec
    return metrics.completion_spread_sec


def compute_disruption(
    schedule: CandidateSchedule, previous: CandidateSchedule, problem: SchedulingProblem
) -> ScheduleDisruption:
    fixed = {t for e in problem.fixed_executions for t in e.task_ids}
    old = {t: a for a in previous.assignments for t in a.task_ids}
    current = {t: a for a in schedule.assignments for t in a.task_ids}
    common: set[TaskId] = old.keys() & current.keys() - fixed
    shifted, resources, groups = 0, 0, 0
    for task in common:
        first, second = old[task], current[task]
        shifted += abs(second.interval.start_sec - first.interval.start_sec)
        before = {
            (u.resource_type, u.physical_resource_id, u.component_id, u.resource_id)
            for u in first.resource_uses
        }
        after = {
            (u.resource_type, u.physical_resource_id, u.component_id, u.resource_id)
            for u in second.resource_uses
        }
        resources += before != after
        groups += set(first.task_ids) != set(second.task_ids)
    return ScheduleDisruption(
        time_shift_sec=shifted, resource_changes=resources, group_changes=groups
    )
