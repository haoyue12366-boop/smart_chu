from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pydantic import AwareDatetime, model_validator

from app.domain.base import MAX_INT, FrozenModel, NonNegativeInt


def checked_seconds(value: int) -> int:
    if type(value) is not int:
        raise TypeError("时间必须是整数秒")
    if abs(value) > MAX_INT:
        raise ValueError("时间超过整数边界")
    return value


def align_up(value_sec: int, grid_sec: int) -> int:
    checked_seconds(value_sec)
    checked_seconds(grid_sec)
    if grid_sec <= 0:
        raise ValueError("网格必须为正")
    return checked_seconds(-(-value_sec // grid_sec) * grid_sec)


def align_down(value_sec: int, grid_sec: int) -> int:
    checked_seconds(value_sec)
    checked_seconds(grid_sec)
    if grid_sec <= 0:
        raise ValueError("网格必须为正")
    return checked_seconds(value_sec // grid_sec * grid_sec)


class TimeOrigin(FrozenModel):
    start_at: AwareDatetime

    def at(self, offset_sec: int) -> datetime:
        return (
            self.start_at.astimezone(UTC) + timedelta(seconds=checked_seconds(offset_sec))
        ).astimezone(self.start_at.tzinfo)

    def offset(self, timestamp: datetime) -> int:
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("实际时刻必须含时区")
        delta = timestamp.astimezone(UTC) - self.start_at.astimezone(UTC)
        if delta.microseconds:
            raise ValueError("相对偏移必须是整数秒，不可截断")
        return checked_seconds(delta.days * 86400 + delta.seconds)


class Interval(FrozenModel):
    start_sec: NonNegativeInt
    end_sec: NonNegativeInt

    @model_validator(mode="after")
    def ordered(self) -> Interval:
        if self.end_sec < self.start_sec:
            raise ValueError("区间结束不能早于开始")
        return self

    def overlaps(self, other: Interval) -> bool:
        return (
            self.start_sec < self.end_sec
            and other.start_sec < other.end_sec
            and self.start_sec < other.end_sec
            and other.start_sec < self.end_sec
        )
