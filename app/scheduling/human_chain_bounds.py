"""利用无条件依赖、互斥覆盖及时间域排除已证明不可能的人工先后。"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from app.domain.carrier_timing import member_offsets

if TYPE_CHECKING:
    from app.scheduling.model_builder import ModelBuilder


class HumanChainBounds:
    def __init__(self, builder: ModelBuilder) -> None:
        self.phases = builder.human_intervals
        carriers = {carrier.carrier_id: carrier for carrier in builder.candidates}
        self.covers = {
            identity: frozenset(task.root for task in carrier.covers)
            for identity, carrier in carriers.items()
        }
        self.phase_tasks: list[str | None] = []
        self.durations: list[int] = []
        self.end_lowers: list[int] = []
        self.start_uppers: list[int] = []
        tasks = {task.task_id: task for task in builder.problem.logical_tasks}
        for phase in self.phases:
            builder.check_budget()
            carrier = carriers.get(phase.carrier_id) if phase.carrier_id is not None else None
            duration = 0
            source_task = None
            lower_end = phase.end.proto.domain[0]
            # 原生 repeated 字段不支持 Python 负索引，必须使用非负位置。
            upper_start = phase.start.proto.domain[len(phase.start.proto.domain) - 1]
            if carrier is not None:
                end_offset = (
                    phase.end_offset_sec
                    if phase.end_offset_sec is not None
                    else carrier.duration_sec
                )
                duration = max(0, end_offset - phase.start_offset_sec)
                if len(carrier.covers) == 1:
                    task_id = carrier.covers[0]
                    task_start, task_end = member_offsets(carrier)[task_id]
                    if task_start <= phase.start_offset_sec <= end_offset <= task_end:
                        source_task = task_id.root
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
                        upper_start = min(
                            upper_start, maximum_end - task_end + phase.start_offset_sec
                        )
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
        for dependency in builder.problem.dependencies:
            pair = {dependency.predecessor_id.root, dependency.successor_id.root}
            if dependency.min_lag_sec < 0 or any(pair <= group for group in conditional_groups):
                continue
            following[dependency.predecessor_id.root].add(dependency.successor_id.root)
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
