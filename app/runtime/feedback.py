"""反馈必须明确提交；预计结束只能生成提醒。"""

from datetime import datetime, timedelta

from app.domain.events import RuntimeEvent
from app.domain.ports import Notification
from app.domain.runtime_session import RuntimeSession


class FeedbackAdapter:
    def event(
        self,
        session: RuntimeSession,
        event_id: str,
        kind: str,
        payload: dict[str, object],
        occurred_at: datetime,
    ) -> RuntimeEvent:
        state = session.runtime
        return RuntimeEvent.model_validate(
            {
                "event_id": event_id,
                "session_id": state.session_id,
                "event_type": kind,
                "payload": payload,
                "occurred_at": occurred_at,
                "received_at": occurred_at,
                "source": state.execution_mode,
                "expected_state_revision": state.state_revision,
                "base_plan_version": state.current_plan_version,
            }
        )

    def overdue(self, session: RuntimeSession, at: datetime) -> tuple[Notification, ...]:
        values = []
        for execution in session.runtime.executions:
            if (
                execution.status != "RUNNING"
                or execution.remaining_observed_at is None
                or execution.remaining_sec is None
            ):
                continue
            predicted = execution.remaining_observed_at + timedelta(seconds=execution.remaining_sec)
            if at < predicted:
                continue
            identity = (
                f"overdue:{session.runtime.session_id.root}:{execution.execution_id.root}:"
                f"{execution.remaining_source_ref}"
            )
            values.append(
                Notification(
                    notification_id=identity,
                    session_id=session.runtime.session_id,
                    plan_version=session.runtime.current_plan_version,
                    deduplication_key=identity,
                    trigger_at=predicted,
                    text="操作已到预计结束时间，请确认是否完成；如未完成，请更新剩余时间。",
                )
            )
        return tuple(values)
