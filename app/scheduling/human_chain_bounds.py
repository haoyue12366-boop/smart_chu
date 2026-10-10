"""利用全部备选的保守时间界，省去不可能先后及必有长间隔的人工连接。"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import TYPE_CHECKING

from app.domain.carrier_timing import member_offsets

if TYPE_CHECKING:
    from app.scheduling.model_builder import ModelBuilder


class HumanChainBounds:
    def __init__(self, builder: ModelBuilder, *, include_rest_bounds: bool = True) -> None:
        self.phases = builder.human_intervals
        self.rest_gap_sec = builder.problem.policy.objective.rest_gap_sec
        carriers = {carrier.carrier_id: carrier for carrier in builder.candidates}
        offsets = {identity: member_offsets(carrier) for identity, carrier in carriers.items()}
        minimum_durations: dict[str, int] = {}
        if include_rest_bounds:
            for spans in offsets.values():
                builder.check_budget()
                for duration_task, (start, end) in spans.items():
                    duration = max(0, end - start)
                    minimum_durations[duration_task.root] = min(
                        minimum_durations.get(duration_task.root, duration), duration
                    )
            for fixed_task, (start, end) in builder.fixed.items():
                minimum_durations[fixed_task.root] = max(0, end - start)
        self.covers = {
            identity: frozenset(task.root for task in carrier.covers)
            for identity, carrier in carriers.items()
        }
        self.phase_tasks: list[str | None] = []
        self.durations: list[int] = []
        self.end_lowers: list[int] = []
        self.start_uppers: list[int] = []
        self.start_lowers: list[int] = []
        self.end_uppers: list[int] = []
        self.end_offsets: list[int | None] = []
        self.phase_heads: list[int] = []
        self.phase_tails: list[int] = []
        tasks = {task.task_id: task for task in builder.problem.logical_tasks}
        for phase in self.phases:
            builder.check_budget()
            carrier = carriers.get(phase.carrier_id) if phase.carrier_id is not None else None
            duration = 0
            source_task = None
            head, tail = 0, 0
            end_offset = phase.end_offset_sec
            lower_end = phase.end.proto.domain[0]
            lower_start = phase.start.proto.domain[0]
            # 原生 repeated 字段不支持 Python 负索引，必须使用非负位置。
            upper_start = phase.start.proto.domain[len(phase.start.proto.domain) - 1]
            upper_end = phase.end.proto.domain[len(phase.end.proto.domain) - 1]
            if carrier is not None:
                end_offset = (
                    phase.end_offset_sec
                    if phase.end_offset_sec is not None
                    else carrier.duration_sec
                )
                duration = max(0, end_offset - phase.start_offset_sec)
                if len(carrier.covers) == 1:
                    task_id = carrier.covers[0]
                    task_start, task_end = offsets[carrier.carrier_id][task_id]
                    if task_start <= phase.start_offset_sec <= end_offset <= task_end:
                        source_task = task_id.root
                        head = phase.start_offset_sec - task_start
                        tail = task_end - end_offset
                        if task_id in builder.fixed:
                            minimum_start, maximum_end = builder.fixed[task_id]
                        else:
                            task_model = tasks[task_id]
                            minimum_start = max(
                                task_model.earliest_start_sec,
                                builder.problem.runtime.now_offset_sec,
                            )
                            maximum_end = (
                                task_model.latest_end_sec
                                if task_model.latest_end_sec is not None
                                else builder.problem.horizon_sec
                            )
                        lower_end = max(lower_end, minimum_start + end_offset - task_start)
                        lower_start = max(lower_start, minimum_start + head)
                        upper_start = min(
                            upper_start, maximum_end - task_end + phase.start_offset_sec
                        )
                        upper_end = min(upper_end, maximum_end - tail)
            elif (
                phase.start.proto.domain[0]
                == phase.start.proto.domain[len(phase.start.proto.domain) - 1]
                and phase.end.proto.domain[0]
                == phase.end.proto.domain[len(phase.end.proto.domain) - 1]
            ):
                duration = max(0, phase.end.proto.domain[0] - phase.start.proto.domain[0])
            self.phase_tasks.append(source_task)
            self.durations.append(duration)
            self.end_lowers.append(lower_end)
            self.start_uppers.append(upper_start)
            self.start_lowers.append(lower_start)
            self.end_uppers.append(upper_end)
            self.end_offsets.append(end_offset)
            self.phase_heads.append(head)
            self.phase_tails.append(tail)

        # 与基础模型相同，库存覆盖内部边及整组已满足边不是无条件时间先后。
        conditional_groups = [
            frozenset(task.root for task in item.covers)
            for item in builder.problem.inventory_supply_candidates
        ]
        conditional_groups.extend(
            frozenset(task.root for task in item.task_ids)
            for item in builder.problem.fixed_task_fulfillments
        )
        following: dict[str, set[str]] = defaultdict(set)
        lags: dict[str, dict[str, int]] = defaultdict(dict)
        for dependency in builder.problem.dependencies:
            builder.check_budget()
            pair = {dependency.predecessor_id.root, dependency.successor_id.root}
            if dependency.min_lag_sec < 0 or any(pair <= group for group in conditional_groups):
                continue
            left, right = dependency.predecessor_id.root, dependency.successor_id.root
            following[left].add(right)
            lags[left][right] = max(lags[left].get(right, 0), dependency.min_lag_sec)
        self.reachable: dict[str, set[str]] = {}
        for task in {task for task in self.phase_tasks if task is not None}:
            builder.check_budget()
            reached: set[str] = set()
            pending = list(following.get(task, ()))
            while pending:
                builder.check_budget()
                other = pending.pop()
                if other not in reached:
                    reached.add(other)
                    pending.extend(following.get(other, ()))
            self.reachable[task] = reached
        self.path_lags = (
            _rest_paths(builder, set(self.reachable), following, lags, minimum_durations)
            if include_rest_bounds
            else {}
        )

        longest_by_carrier: dict[str, int] = defaultdict(int)
        always = []
        for phase, duration in zip(self.phases, self.durations, strict=True):
            if phase.carrier_id is not None:
                root = phase.carrier_id.root
                longest_by_carrier[root] = max(longest_by_carrier[root], duration)
            if phase.presence.proto.domain[0] == 1:
                always.append(duration)
        options: dict[str, list[int]] = defaultdict(list)
        for carrier in builder.candidates:
            for covered_task in carrier.covers:
                if covered_task not in builder.fixed:
                    options[covered_task.root].append(longest_by_carrier[carrier.carrier_id.root])
        self.minimum_busy_sec = max([0, *always, *(min(values) for values in options.values())])

    def can_follow(self, left_index: int, right_index: int) -> bool:
        left, right = self.phases[left_index], self.phases[right_index]
        if self.end_lowers[left_index] > self.start_uppers[right_index]:
            return False
        if left.carrier_id is not None and right.carrier_id is not None:
            if left.carrier_id != right.carrier_id:
                if self.covers[left.carrier_id] & self.covers[right.carrier_id]:
                    return False
            elif left.end_offset_sec is not None and left.end_offset_sec > right.start_offset_sec:
                return False
        before, after = self.phase_tasks[right_index], self.phase_tasks[left_index]
        return not (
            before is not None
            and after is not None
            and after in self.reachable[before]
            and (self.durations[left_index] > 0 or self.durations[right_index] > 0)
        )

    def must_rest(self, left_index: int, right_index: int) -> bool:
        """两段都在场且按该方向执行时，是否已保证至少阈值长度的间隔。"""
        if self.rest_gap_sec == 0:
            return True  # 单人人工 NoOverlap 已保证选中先后之间的非负间隔。
        left, right = self.phases[left_index], self.phases[right_index]
        end_offset = self.end_offsets[left_index]
        if (
            left.carrier_id is not None
            and left.carrier_id == right.carrier_id
            and end_offset is not None
            and right.start_offset_sec - end_offset >= self.rest_gap_sec
        ):
            return True
        if self.start_lowers[right_index] - self.end_uppers[left_index] >= self.rest_gap_sec:
            return True
        before, after = self.phase_tasks[left_index], self.phase_tasks[right_index]
        lag = (
            self.path_lags.get(before, {}).get(after)
            if before is not None and after is not None
            else None
        )
        return lag is not None and (
            lag + self.phase_tails[left_index] + self.phase_heads[right_index] >= self.rest_gap_sec
        )


def _rest_paths(
    builder: ModelBuilder,
    sources: set[str],
    following: dict[str, set[str]],
    lags: dict[str, dict[str, int]],
    minimum_durations: dict[str, int],
) -> dict[str, dict[str, int]]:
    """DAG 中 End(before)→Start(after) 的安全下界；循环或缺时长不推定额外等待。"""
    nodes = set(following) | {right for rights in following.values() for right in rights}
    incoming = dict.fromkeys(nodes, 0)
    for rights in following.values():
        builder.check_budget()
        for right in rights:
            incoming[right] += 1
    pending = deque(sorted(node for node, count in incoming.items() if count == 0))
    ordered = []
    while pending:
        builder.check_budget()
        node = pending.popleft()
        ordered.append(node)
        for right in following.get(node, ()):
            incoming[right] -= 1
            if incoming[right] == 0:
                pending.append(right)
    if len(ordered) != len(nodes):
        return {}
    paths: dict[str, dict[str, int]] = {}
    for source in sources:
        builder.check_budget()
        gaps = dict(lags.get(source, {}))
        for node in ordered:
            builder.check_budget()
            if node not in gaps:
                continue
            lower_end = gaps[node] + minimum_durations.get(node, 0)
            for right, lag in lags.get(node, {}).items():
                gaps[right] = max(gaps.get(right, 0), lower_end + lag)
        paths[source] = gaps
    return paths
