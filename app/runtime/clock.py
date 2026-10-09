"""业务时间可模拟；求解预算始终使用真实单调时钟。"""

import time
from datetime import UTC, datetime, timedelta


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic_ns(self) -> int:
        return time.monotonic_ns()


class SimulationClock(SystemClock):
    def __init__(self, origin: datetime) -> None:
        if origin.tzinfo is None:
            raise ValueError("模拟原点必须有时区")
        self.origin = origin
        self.offset_sec = 0

    def now(self) -> datetime:
        return self.origin + timedelta(seconds=self.offset_sec)

    def advance(self, to_offset_sec: int) -> None:
        if type(to_offset_sec) is not int or to_offset_sec < self.offset_sec:
            raise ValueError("模拟时间只能向前推进整数秒")
        self.offset_sec = to_offset_sec
