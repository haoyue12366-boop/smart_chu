"""明确标记的排程时钟；首次成功发布后计时，沿用会话原时间坐标。"""

from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import AwareDatetime

from app.domain.base import FrozenModel, NonNegativeInt
from app.domain.ids import TaskId

if TYPE_CHECKING:
    from app.domain.runtime_session import RuntimeSession


class ScheduleClockState(FrozenModel):
    started_at: AwareDatetime
    start_offset_sec: NonNegativeInt
    processed_until_sec: NonNegativeInt
    replan_requested_sec: NonNegativeInt | None = None
    replan_not_before_sec: NonNegativeInt | None = None
    replan_continuation_task_ids: tuple[TaskId, ...] = ()


def clock_offset(session: "RuntimeSession", now: datetime) -> int:
    """时钟推断与墙钟相隔首次发布前耗时，重排和重启不重置锚点。"""
    clock = session.schedule_clock
    if clock is None:
        return session.runtime.now_offset_sec
    elapsed = now - clock.started_at
    return clock.start_offset_sec + max(0, elapsed.days * 86_400 + elapsed.seconds)
