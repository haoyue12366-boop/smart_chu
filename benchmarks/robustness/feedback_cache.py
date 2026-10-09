"""完整观察键与不可改写磁盘证据仍保留，内存只缓存少量最近状态。"""

import gzip
import hashlib
import json
from collections import OrderedDict
from collections.abc import Iterator, MutableMapping
from pathlib import Path
from typing import TypeVar

from app.domain.reports import PlanningResult
from app.domain.runtime_session import RuntimeSession
from app.domain.schedule import ValidatedSchedule
from app.domain.scheduling_problem import SchedulingProblem
from benchmarks.robustness.observed_session import bind_plan
from benchmarks.robustness.simulator import ObservationCache

K = TypeVar("K")
V = TypeVar("V")
CacheValue = tuple[RuntimeSession | None, SchedulingProblem | None, dict[str, object]]


class BoundedMemo(OrderedDict[K, V]):
    def __init__(self, limit: int) -> None:
        super().__init__()
        if limit <= 0:
            raise ValueError("缓存上限须为正数")
        self.limit = limit

    def __getitem__(self, key: K) -> V:
        value = super().__getitem__(key)
        self.move_to_end(key)
        return value

    def __setitem__(self, key: K, value: V) -> None:
        super().__setitem__(key, value)
        self.move_to_end(key)
        while len(self) > self.limit:
            self.popitem(last=False)


class BoundedObservationCache(ObservationCache):
    def __init__(self, limit: int = 256) -> None:
        super().__init__()
        self.transitions = BoundedMemo(limit)
        self.final_problems = BoundedMemo(8)


class ObservedDiskCache(MutableMapping[str, CacheValue]):
    """父规划器先写归档再登记；冷命中只重放原成功绑定，不重新求解。"""

    def __init__(self, output: Path, limit: int = 16) -> None:
        self.output = output
        self.hot: BoundedMemo[str, CacheValue] = BoundedMemo(limit)
        self.file_hashes: dict[str, str] = {}

    def path(self, key: str) -> Path:
        if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            raise ValueError("观察键必须是 SHA256")
        return self.output / f"replan-{key}.json.gz"

    def artifact(self, key: str) -> dict[str, object]:
        payload = self.path(key).read_bytes()
        if hashlib.sha256(payload).hexdigest() != self.file_hashes[key]:
            raise ValueError("缓存归档身份发生变化")
        return json.loads(gzip.decompress(payload))

    def __contains__(self, key: object) -> bool:
        return key in self.file_hashes

    def __len__(self) -> int:
        return len(self.file_hashes)

    def __iter__(self) -> Iterator[str]:
        return iter(self.file_hashes)

    def __delitem__(self, key: str) -> None:
        raise TypeError("观察证据登记不可删除")

    def __setitem__(self, key: str, value: CacheValue) -> None:
        sha = hashlib.sha256(self.path(key).read_bytes()).hexdigest()
        if key in self.file_hashes and self.file_hashes[key] != sha:
            raise ValueError("同一观察键不能改写原计算证据")
        self.file_hashes[key] = sha
        self.hot[key] = value

    def __getitem__(self, key: str) -> CacheValue:
        if key in self.hot:
            return self.hot[key]
        frozen = self.artifact(key)
        problem = SchedulingProblem.model_validate(frozen["problem"]) if frozen["problem"] else None
        result = PlanningResult.model_validate(frozen["result"]) if frozen["result"] else None
        measurement = frozen["measurement"]
        if not isinstance(measurement, dict):
            raise ValueError("缓存缺少实际计算记录")
        updated = None
        if measurement["failure"] is None:
            if (
                problem is None
                or result is None
                or result.candidate is None
                or result.validation is None
                or not result.validation.valid
            ):
                raise ValueError("缓存成功结果缺少完整独立校验证据")
            observed = RuntimeSession.model_validate(frozen["observed"])
            updated = bind_plan(
                observed,
                problem,
                ValidatedSchedule(candidate=result.candidate, validation=result.validation),
            )
        value = updated, problem, measurement
        self.hot[key] = value
        return value
