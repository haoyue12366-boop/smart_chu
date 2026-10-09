"""载体的纯时间投影；工艺合法性由独立Validator另行核对。"""

from app.domain.ids import TaskId
from app.domain.schedule import ScheduledAssignment
from app.domain.scheduling_problem import CandidateCarrier, SchedulingProblem
from app.domain.time import Interval


def member_offsets(carrier: CandidateCarrier) -> dict[TaskId, tuple[int, int]]:
    if carrier.member_offsets:
        return {p.task_id: (p.start_offset_sec, p.end_offset_sec) for p in carrier.member_offsets}
    return {t: (0, carrier.duration_sec) for t in carrier.covers}


def task_intervals(
    problem: SchedulingProblem, assignments: tuple[ScheduledAssignment, ...]
) -> dict[TaskId, Interval]:
    """投影已选择载体的逻辑端口；事实合法性仍由独立校验负责。"""
    carriers = {
        c.carrier_id: c
        for c in (
            *problem.standalone_candidates,
            *problem.shared_prep_candidates,
            *problem.thermal_batch_candidates,
            *problem.inventory_supply_candidates,
        )
    }
    result = {}
    for assignment in assignments:
        offsets = member_offsets(carriers[assignment.carrier_id])
        for tid in assignment.task_ids:
            lo, hi = offsets[tid]
            result[tid] = Interval(
                start_sec=assignment.interval.start_sec + lo,
                end_sec=assignment.interval.start_sec + hi,
            )
    return result
