"""固定白名单的只读资源诊断；未知内核数据不推断为零或无故障。"""

import math
import mmap
import os
import re
import time
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4


def _text(path: Path) -> str | None:
    try:
        with path.open("rb") as stream:
            body = stream.read(16_385)
        return body.decode("ascii").strip() if len(body) <= 16_384 else None
    except (OSError, UnicodeError):
        return None


def _number(value: str | None) -> int | None:
    try:
        result = int(value) if value is not None else -1
        return result if result >= 0 else None
    except ValueError:
        return None


def _counters(value: str | None, allowed: frozenset[str]) -> dict[str, int] | None:
    result = {}
    for line in (value or "").splitlines():
        pair = line.split()
        if len(pair) == 2 and pair[0] in allowed:
            number = _number(pair[1])
            if number is not None:
                result[pair[0]] = number
    return result or None


class ResourceMonitor:
    def __init__(
        self,
        *,
        proc_root: Path = Path("/proc"),
        cgroup_root: Path = Path("/sys/fs/cgroup"),
        page_bytes: int = mmap.PAGESIZE,
        clock_ns: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        self._proc_root, self._cgroup_root = proc_root, cgroup_root
        self._page_bytes, self._clock_ns = page_bytes, clock_ns
        self._started_ns = clock_ns()
        self._boot_id = uuid4().hex
        commit = os.getenv("RENDER_GIT_COMMIT", "")
        self._commit = commit if re.fullmatch(r"[0-9a-fA-F]{40}", commit) else None
        try:
            cpu = float(os.getenv("RENDER_CPU_COUNT", ""))
            self._render_cpu = cpu if math.isfinite(cpu) and cpu > 0 else None
        except ValueError:
            self._render_cpu = None

    def _rss(self, process_id: int | None) -> int | None:
        if process_id is None:
            return None
        values = (_text(self._proc_root / str(process_id) / "statm") or "").split()
        pages = _number(values[1]) if len(values) >= 2 else None
        return pages * self._page_bytes if pages is not None else None

    def _group(self) -> Path:
        # namespace可能已把当前组挂成根，也可能仍显示相对挂载根的组路径。
        binding = _text(self._proc_root / str(os.getpid()) / "cgroup") or ""
        root = self._cgroup_root.resolve()
        for line in binding.splitlines():
            fields = line.split(":", 2)
            if len(fields) == 3 and fields[1] == "":
                group = (root / fields[2].lstrip("/")).resolve()
                if (group == root or root in group.parents) and (
                    group / "memory.current"
                ).is_file():
                    return group
        return root

    def read(self, *, worker_process_id: int | None = None) -> dict[str, object]:
        root = self._group()
        maximum = _text(root / "memory.max")
        maximum_bytes = _number(maximum)
        unlimited = True if maximum == "max" else False if maximum_bytes is not None else None
        cpu = (_text(root / "cpu.max") or "").split()
        quota, period = (_number(cpu[0]), _number(cpu[1])) if len(cpu) == 2 else (None, None)
        return {
            "boot_id": self._boot_id,
            "uptime_ms": max(0, self._clock_ns() - self._started_ns) // 1_000_000,
            "commit_sha": self._commit,
            "render_cpu_count": self._render_cpu,
            "api_rss_bytes": self._rss(os.getpid()),
            "solver_rss_bytes": self._rss(worker_process_id),
            "cgroup_memory_current_bytes": _number(_text(root / "memory.current")),
            "cgroup_memory_peak_bytes": _number(_text(root / "memory.peak")),
            "cgroup_memory_max_bytes": maximum_bytes,
            "cgroup_memory_max_unlimited": unlimited,
            "cgroup_memory_events": _counters(
                _text(root / "memory.events"),
                frozenset({"low", "high", "max", "oom", "oom_kill", "oom_group_kill"}),
            ),
            "cgroup_cpu_quota_usec": quota,
            "cgroup_cpu_period_usec": period,
            "cgroup_cpu_stat": _counters(
                _text(root / "cpu.stat"),
                frozenset(
                    {
                        "usage_usec",
                        "user_usec",
                        "system_usec",
                        "nr_periods",
                        "nr_throttled",
                        "throttled_usec",
                    }
                ),
            ),
        }
