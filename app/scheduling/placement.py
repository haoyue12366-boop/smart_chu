"""在给定布局中按冲突事件跳点，寻找所有资源共同空档。"""

import time

from app.domain.carrier_timing import task_intervals
from app.domain.ports import Deadline
from app.domain.recovery import retained_input_tasks
from app.domain.schedule import ScheduledAssignment
from app.domain.scheduling_problem import CandidateCarrier, SchedulingProblem
from app.domain.time import Interval, align_up
from app.scheduling.calendar_projection import capacity_collision, conflicts, entries_for
from app.scheduling.calendar_types import (
    MaterialAllocation,
    PlacementResult,
    TaskPort,
    VirtualOutput,
)
from app.scheduling.calendars import CalendarState
from app.scheduling.inventory_placement import inventory_rejections
from app.scheduling.transition_placement import transition_rejections


def find_earliest_feasible_placement(
    candidate: CandidateCarrier,
    calendars: CalendarState,
    problem: SchedulingProblem,
    deadline: Deadline,
) -> PlacementResult:
    assignment = ScheduledAssignment(
        carrier_id=candidate.carrier_id,
        task_ids=candidate.covers,
        interval=Interval(start_sec=0, end_sec=candidate.duration_sec),
        resource_uses=candidate.resource_uses,
    )
    return find_layout_placement(
        (assignment,), calendars, problem, deadline, lock_frozen_anchor=False
    )


def find_layout_placement(
    layout: tuple[ScheduledAssignment, ...],
    calendars: CalendarState,
    problem: SchedulingProblem,
    deadline: Deadline,
    *,
    lock_frozen_anchor: bool = True,
    latest_anchor_sec: int | None = None,
) -> PlacementResult:
    if not layout:
        return PlacementResult(rejection_reasons=("布局为空",))
    offsets = task_intervals(problem, layout)
    known = {p.task_id: p.interval for p in calendars.current.ports}
    if offsets.keys() & known.keys():
        return PlacementResult(rejection_reasons=("布局重复覆盖已安排或冻结任务",))
    tasks = {t.task_id: t for t in problem.logical_tasks}
    lo, hi = 0, problem.horizon_sec
    frozen_instances = {
        tasks[t].recipe_instance_id
        for e in problem.fixed_executions
        for t in e.task_ids
        if t in tasks
    }
    frozen_instances.update(item.recipe_instance_id for item in problem.fixed_supply_fulfillments)
    frozen_instances.update(item.recipe_instance_id for item in problem.advance_preparations)
    selected = {item.carrier_id for item in layout}
    supplied = {
        task
        for item in problem.inventory_supply_candidates
        if item.carrier_id in selected
        for task in item.covers
    }
    supplied.update(retained_input_tasks(problem.runtime))
    frozen_anchor = lock_frozen_anchor and any(
        tasks[t].recipe_instance_id in frozen_instances for t in offsets
    )
    for tid, port in offsets.items():
        task = tasks[tid]
        lo = max(
            lo,
            task.earliest_start_sec - port.start_sec,
            problem.runtime.now_offset_sec - port.start_sec,
        )
        hi = min(
            hi,
            (task.latest_end_sec if task.latest_end_sec is not None else problem.horizon_sec)
            - port.end_sec,
        )
    for dep in problem.dependencies:
        if dep.successor_id in offsets and dep.predecessor_id not in offsets:
            previous = known.get(dep.predecessor_id)
            if previous is None:
                return PlacementResult(rejection_reasons=("外部前置尚未安排",))
            offset = offsets[dep.successor_id].start_sec
            lo = max(lo, previous.end_sec + dep.min_lag_sec - offset)
            if dep.max_lag_sec is not None:
                hi = min(hi, previous.end_sec + dep.max_lag_sec - offset)
        if dep.predecessor_id in offsets and dep.successor_id in known:
            later = known[dep.successor_id]
            offset = offsets[dep.predecessor_id].end_sec
            hi = min(hi, later.start_sec - dep.min_lag_sec - offset)
            if dep.max_lag_sec is not None:
                lo = max(lo, later.start_sec - dep.max_lag_sec - offset)
    supplies = {s.supply_id: s for s in problem.material_flow.supplies}
    for demand in problem.material_flow.demands:
        if demand.task_id not in offsets or demand.task_id in supplied:
            continue
        supply = supplies[demand.supply_id]
        offset = offsets[demand.task_id].start_sec
        lo = max(lo, supply.available_at_sec - offset)
        if supply.producer_task_id is not None and supply.producer_task_id not in offsets:
            producer = known.get(supply.producer_task_id)
            if producer is None:
                return PlacementResult(rejection_reasons=("生产物料的任务尚未安排",))
            lo = max(lo, producer.end_sec - offset)
    if frozen_anchor:
        hi = min(hi, 0)
    grid = problem.policy.time_grid_sec
    reverse = latest_anchor_sec is not None
    if latest_anchor_sec is not None:
        hi = min(hi, latest_anchor_sec)
    anchor = hi // grid * grid if reverse else align_up(lo, grid)
    limit = problem.policy.greedy_search.max_gap_checks_per_candidate
    for checks in range(1, limit + 1):
        if time.monotonic_ns() >= deadline.expires_at_ns:
            raise TimeoutError("Greedy 共同空档搜索截止时间已到")
        if not lo <= anchor <= hi:
            return PlacementResult(
                rejection_reasons=("当前布局的锚点窗口已耗尽",), gap_checks=checks
            )
        assignments = tuple(
            a.model_copy(
                update={
                    "interval": Interval(
                        start_sec=a.interval.start_sec + anchor, end_sec=a.interval.end_sec + anchor
                    )
                }
            )
            for a in layout
        )
        entries = entries_for(problem, assignments)
        inventory_errors = inventory_rejections(
            problem, (*calendars.current.assignments, *assignments)
        )
        if inventory_errors:
            return PlacementResult(rejection_reasons=inventory_errors, gap_checks=checks)
        jumps = [
            old.interval.start_sec - (new.interval.end_sec - anchor)
            if reverse
            else old.interval.end_sec - (new.interval.start_sec - anchor)
            for new in entries
            for old in calendars.current.entries
            if conflicts(new, old)
        ]
        collision = capacity_collision(problem, (*calendars.current.entries, *entries))
        if collision is not None:
            new_members = set(offsets)
            left, right = collision
            if set(left.task_ids) & new_members and set(right.task_ids) & new_members:
                return PlacementResult(rejection_reasons=("布局内部超出设备层数或温度不兼容",))
            new, old = (left, right) if set(left.task_ids) & new_members else (right, left)
            if not set(new.task_ids) & new_members:
                return PlacementResult(rejection_reasons=("既有设备预约已超出层数或温度不兼容",))
            jumps.append(
                old.interval.start_sec - (new.interval.end_sec - anchor)
                if reverse
                else old.interval.end_sec - (new.interval.start_sec - anchor)
            )
        if not jumps:
            reasons = transition_rejections(problem, (*calendars.current.entries, *entries))
            if reasons:
                return PlacementResult(rejection_reasons=reasons, gap_checks=checks)
            ports = tuple(
                TaskPort(task_id=t, interval=port)
                for t, port in task_intervals(problem, assignments).items()
            )
            all_ports = known | {p.task_id: p.interval for p in ports}
            # 所有已经有端口的关系复核；有限布局不能略过紧窗口。
            for relation in problem.mandatory_programs.time_relations:
                if relation.left_task not in all_ports or relation.right_task not in all_ports:
                    continue
                left_port = all_ports[relation.left_task]
                right_port = all_ports[relation.right_task]
                delta = (
                    right_port.start_sec if relation.right_anchor == "START" else right_port.end_sec
                ) - (left_port.start_sec if relation.left_anchor == "START" else left_port.end_sec)
                if delta < relation.min_offset_sec or (
                    relation.max_offset_sec is not None and delta > relation.max_offset_sec
                ):
                    return PlacementResult(
                        rejection_reasons=("插入违反固定程序时间端口",), gap_checks=checks
                    )
            allocations = tuple(
                MaterialAllocation(demand_id=d.demand_id, supply_id=d.supply_id, task_id=d.task_id)
                for d in problem.material_flow.demands
                if d.task_id in offsets and d.task_id not in supplied
            )
            outputs = tuple(
                VirtualOutput(
                    supply_id=s.supply_id, available_at_sec=all_ports[s.producer_task_id].end_sec
                )
                for s in problem.material_flow.supplies
                if s.producer_task_id in offsets
            )
            return PlacementResult(
                assignments=assignments,
                entries=entries,
                logical_time_mapping=ports,
                material_allocations=allocations,
                virtual_outputs=outputs,
                gap_checks=checks,
            )
        next_anchor = min(jumps) // grid * grid if reverse else align_up(max(jumps), grid)
        if (reverse and next_anchor >= anchor) or (not reverse and next_anchor <= anchor):
            return PlacementResult(rejection_reasons=("空档锚点没有推进",), gap_checks=checks)
        anchor = next_anchor
    return PlacementResult(rejection_reasons=("有限空档检查次数耗尽",), gap_checks=limit)
