"""只合并已证明互斥且时间变量相同的人工目标段，保留原资源约束。"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from typing import TYPE_CHECKING

from app.scheduling.human_chain_bounds import HumanChainBounds

if TYPE_CHECKING:
    from app.scheduling.model_builder import ModelBuilder


class HumanObjectiveGroups:
    def __init__(self, builder: ModelBuilder, bounds: HumanChainBounds) -> None:
        self.bounds = bounds
        self.minimum_busy_sec = bounds.minimum_busy_sec
        originals = builder.human_intervals
        carriers = {carrier.carrier_id: carrier for carrier in builder.candidates}
        groups: dict[tuple[object, ...], list[int]] = defaultdict(list)
        for i, phase in enumerate(originals):
            builder.check_budget()
            carrier = carriers.get(phase.carrier_id) if phase.carrier_id is not None else None
            size = phase.interval.proto.interval.size
            # 共同覆盖的恰好一次约束证明互斥。历史、零长段和未绑定的段不合并。
            eligible = (
                carrier is not None
                and bool(carrier.covers)
                and set(phase.tasks) == set(carrier.covers)
                and not set(carrier.covers) & builder.fixed.keys()
                and phase.carrier_id in builder.selected
                and phase.presence.index == builder.selected[phase.carrier_id].index
                and not size.vars
                and size.offset > 0
            )
            key = (
                (phase.start.index, phase.end.index, tuple(sorted(t.root for t in phase.tasks)))
                if eligible
                else ("unmerged", i)
            )
            groups[key].append(i)
        self.members: list[tuple[int, ...]] = []
        for indices in groups.values():
            if len({originals[i].carrier_id for i in indices}) != len(indices):
                self.members.extend((i,) for i in indices)
            else:
                self.members.append(tuple(indices))
        self.members.sort(key=lambda group: group[0])
        self.phases = []
        for i, members in enumerate(self.members):
            builder.check_budget()
            phase = originals[members[0]]
            if len(members) > 1:
                presence = builder.model.new_bool_var(f"human-group:{i}")
                builder.model.add(presence == sum(originals[j].presence for j in members))
                phase = replace(phase, presence=presence)
            self.phases.append(phase)

    def can_follow(self, left: int, right: int) -> bool:
        # 组的时间界必须覆盖每个合法成员，不能只使用代表备选的时长或依赖。
        return any(
            self.bounds.can_follow(a, b) for a in self.members[left] for b in self.members[right]
        )

    def must_rest(self, left: int, right: int) -> bool:
        return all(
            not self.bounds.can_follow(a, b) or self.bounds.must_rest(a, b)
            for a in self.members[left]
            for b in self.members[right]
        )
