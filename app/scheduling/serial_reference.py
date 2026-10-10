"""同一完整问题的逐道参考候选；独立验证由上层注入 Validator 完成。"""

from typing import Literal

from app.domain.base import FrozenModel, NonEmpty
from app.domain.carrier_timing import task_intervals
from app.domain.ports import Deadline
from app.domain.schedule import CandidateSchedule
from app.domain.scheduling_problem import SchedulingProblem
from app.domain.serial_order import serial_task_groups


class SerialReferenceResult(FrozenModel):
    status: Literal["CANDIDATE_FOUND", "UNAVAILABLE"]
    candidate: CandidateSchedule | None = None
    reason: NonEmpty | None = None


def build_serial_reference(problem: SchedulingProblem, deadline: Deadline) -> SerialReferenceResult:
    from app.scheduling.cp_sat import CpSatScheduler

    result = CpSatScheduler().solve(problem, None, deadline, serial_menu=True)
    if result.candidate is None:
        return SerialReferenceResult(
            status="UNAVAILABLE", reason=result.diagnostic_message or result.status.value
        )
    if not serial_order_holds(result.candidate, problem):
        return SerialReferenceResult(status="UNAVAILABLE", reason="参考候选没有逐道完成菜单")
    return SerialReferenceResult(status="CANDIDATE_FOUND", candidate=result.candidate)


def serial_order_holds(candidate: CandidateSchedule, problem: SchedulingProblem) -> bool:
    ports = task_intervals(problem, candidate.assignments)
    previous_end = problem.runtime.now_offset_sec
    for tasks in serial_task_groups(problem):
        if any(t not in ports for t in tasks):
            return False
        if min(ports[t].start_sec for t in tasks) < previous_end:
            return False
        previous_end = max(ports[t].end_sec for t in tasks)
    return True
