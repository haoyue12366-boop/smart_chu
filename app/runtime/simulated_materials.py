"""显式模拟反馈；来源由 Simulator 标记，不用于生成真实人工测量。"""

from app.domain.ids import TaskId
from app.domain.runtime_session import RuntimeSession
from app.domain.scheduling_problem import SchedulingProblem
from app.runtime.feedback_template import proposed_payload, release_eligible


def simulated_payload(
    session: RuntimeSession,
    problem: SchedulingProblem,
    group: tuple[TaskId, ...],
    execution_id: str,
    *,
    completed: bool,
) -> dict[str, object]:
    result = proposed_payload(
        session, problem, group, execution_id, completed=completed, lot_namespace="sim-lot"
    )
    result["output_status"] = "QUALIFIED" if completed else "UNKNOWN"
    result["resource_release_status"] = (
        "CONFIRMED"
        if completed and release_eligible(session, execution_id, group)
        else "UNCONFIRMED"
    )
    return result
