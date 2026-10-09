"""仅根据已观察事实计算启动窗口；计划时间是窗口内的执行参考。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ObservedDependency:
    predecessor_id: str
    successor_id: str
    actual_end_sec: int | None
    planned_end_sec: int
    min_lag_sec: int
    max_lag_sec: int | None


@dataclass(frozen=True)
class StartWindow:
    earliest_start_sec: int
    latest_start_sec: int | None
    preferred_start_sec: int
    dispatch_at_sec: int | None
    code: str | None = None


def observed_window(
    now_sec: int,
    not_before_sec: int,
    preferred_start_sec: int,
    dependencies: tuple[ObservedDependency, ...],
) -> StartWindow:
    known = tuple((d, d.actual_end_sec) for d in dependencies if d.actual_end_sec is not None)
    earliest = max(
        now_sec,
        not_before_sec,
        *(end + d.min_lag_sec for d, end in known),
    )
    limits = tuple(end + d.max_lag_sec for d, end in known if d.max_lag_sec is not None)
    latest = min(limits) if limits else None
    code = None
    if latest is not None and now_sec > latest:
        code = "ACTUAL_WINDOW_EXPIRED"
    elif latest is not None and earliest > latest:
        code = "START_WINDOW_EMPTY"
    elif len(known) != len(dependencies):
        code = "OBSERVATION_REQUIRED"
    at = max(earliest, preferred_start_sec)
    if latest is not None:
        at = min(at, latest)
    return StartWindow(earliest, latest, preferred_start_sec, at if code is None else None, code)
