"""时钟同步和编译独立限时；实测开销不扣求解/发布余额。"""

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from app.domain.ports import Deadline


@dataclass
class PreparationBudget:
    monotonic_ns: Callable[[], int] = time.monotonic_ns
    started_ns: int | None = None
    excluded_ns: int = 0

    def extend(self, deadline: Deadline) -> Deadline:
        return Deadline(expires_at_ns=deadline.expires_at_ns + self.excluded_ns)

    @contextmanager
    def measure(self, max_ms: int) -> Iterator[Deadline]:
        """只补回实际用掉的准备时间，单次准备仍有有限截止。"""
        started = self.monotonic_ns()
        limit = Deadline(expires_at_ns=started + max_ms * 1_000_000)
        try:
            yield limit
            if self.monotonic_ns() >= limit.expires_at_ns:
                raise TimeoutError("准备阶段超过独立截止时间")
        finally:
            self.excluded_ns += max(0, self.monotonic_ns() - started)
