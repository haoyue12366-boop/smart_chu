"""事件身份与来源检查。接收时间不属于客户端幂等内容。"""

import hashlib
import json

from app.domain.events import RuntimeEvent
from app.domain.runtime_session import RuntimeSession


class EventConflict(ValueError):
    pass


def event_digest(event: RuntimeEvent) -> str:
    payload = event.model_dump(mode="json", exclude={"received_at"})
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def validate_event(event: RuntimeEvent, session: RuntimeSession) -> None:
    state = session.runtime
    if (
        event.expected_state_revision != state.state_revision
        or event.base_plan_version != state.current_plan_version
    ):
        raise EventConflict("事件预期状态或计划版本已过期")
    if session.status == "ENDED":
        raise ValueError("会话已经结束")
    correcting_clock = state.execution_mode == "SCHEDULE_CLOCK" and event.source in {
        "MANUAL_CONFIRM",
        "DEVICE_FEEDBACK",
    }
    if event.source != state.execution_mode and not correcting_clock:
        raise ValueError("事件来源与会话执行模式不一致")
    if event.occurred_at < state.time_origin.start_at or event.occurred_at > event.received_at:
        raise ValueError("事件时刻早于会话或晚于接收时间")
    if event.event_type == "START_SESSION" and session.status != "CREATED":
        raise ValueError("会话只能开始一次")
    if event.event_type != "START_SESSION" and session.status == "CREATED":
        raise ValueError("会话尚未开始")
