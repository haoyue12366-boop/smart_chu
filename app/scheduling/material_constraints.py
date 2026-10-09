"""候选产出与分配随载体选择存在；数量使用有理份额的整数缩放。"""

from __future__ import annotations

from collections import defaultdict
from fractions import Fraction
from math import lcm
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.scheduling.model_builder import ModelBuilder


def add_material_allocation_constraints(builder: ModelBuilder) -> None:
    allocation = builder.problem.material_allocations
    if not allocation.allocations and not allocation.remainders:
        return
    supplies = {s.supply_id: s for s in builder.problem.material_flow.supplies}
    demands = {d.demand_id: d for d in builder.problem.material_flow.demands}
    scale = 1
    for denominator in [a.share_denominator for a in allocation.allocations] + [
        r.share_denominator for r in allocation.remainders
    ]:
        scale = lcm(scale, denominator)
        if scale > 2**40:
            raise ValueError("数量精确缩放超出安全整数范围")
    by_demand = defaultdict(list)
    by_supply = defaultdict(list)
    seen = set()
    for a in allocation.allocations:
        builder.check_budget()
        key = (a.producer_carrier_id, a.demand_id)
        if (
            key in seen
            or a.producer_carrier_id not in builder.selected
            or a.demand_id not in demands
        ):
            raise ValueError("计划物料分配重复或引用悬空")
        seen.add(key)
        units = scale * a.share_numerator // a.share_denominator
        if units > scale:
            raise ValueError("单项分配超过净产量")
        var = builder.model.new_int_var(
            0, units, f"allocation:{a.producer_carrier_id.root}:{a.demand_id}"
        )
        producer = builder.selected[a.producer_carrier_id]
        consumer = builder.performed(demands[a.demand_id].task_id)
        builder.model.add(var <= units * producer)
        builder.model.add(var <= units * consumer)
        builder.model.add(var >= units * (producer + consumer - 1))
        builder.allocation_amounts[key] = var
        by_demand[a.demand_id].append(var)
        by_supply[a.supply_id].append(var)
    for ident, variables in by_demand.items():
        demand = demands[ident]
        amount = Fraction(demand.share_numerator, demand.share_denominator) * scale
        if amount.denominator != 1:
            raise ValueError("需求不能在数量缩放上表达")
        builder.model.add(sum(variables) == amount.numerator * builder.performed(demand.task_id))
    for ident, variables in by_supply.items():
        if ident not in supplies:
            raise ValueError("分配供应不存在")
        builder.model.add(sum(variables) <= scale)
    for r in allocation.remainders:
        if r.producer_carrier_id not in builder.selected or r.supply_id not in supplies:
            raise ValueError("计划余量引用悬空")
        units = scale * r.share_numerator // r.share_denominator
        if units > scale:
            raise ValueError("计划余量超过净产量")
        # Remaining quantity is a conditional plan expression, never a runtime lot.
        variables = [
            v
            for (carrier, demand), v in builder.allocation_amounts.items()
            if carrier == r.producer_carrier_id and demands[demand].supply_id == r.supply_id
        ]
        builder.model.add(
            sum(variables) + units * builder.selected[r.producer_carrier_id]
            == scale * builder.selected[r.producer_carrier_id]
        )
