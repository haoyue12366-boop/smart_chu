"""库存候选与普通需求共享同一批实物数量，所有系数精确整数化。"""

from __future__ import annotations

from collections import defaultdict
from fractions import Fraction
from math import lcm
from typing import TYPE_CHECKING

from app.domain.quantity import ScaledQuantity, Unit
from app.domain.recovery import retained_input_tasks

if TYPE_CHECKING:
    from ortools.sat.python import cp_model

    from app.scheduling.model_builder import ModelBuilder


def add_inventory_constraints(builder: ModelBuilder) -> None:
    problem = builder.problem
    details = problem.runtime.details
    if details is None or not problem.inventory_supply_candidates:
        return
    lots = {lot.lot_id: lot for lot in details.lots}
    by_lot: dict[str, list[tuple[Fraction, cp_model.LinearExpr | int]]] = defaultdict(list)
    by_pool: dict[str, list[tuple[Fraction, cp_model.LinearExpr | int]]] = defaultdict(list)
    pool_units: dict[str, Unit] = {}
    for candidate in problem.inventory_supply_candidates:
        binding = candidate.inventory_supply
        assert binding is not None
        selected = builder.selected[candidate.carrier_id]
        builder.model.add(
            builder.carrier_starts[candidate.carrier_id] >= binding.available_at_sec
        ).only_enforce_if(selected)
        consumers = [
            demand.task_id
            for demand in problem.material_flow.demands
            if demand.supply_id == binding.target_supply_id
            and demand.task_id not in candidate.covers
        ]
        if binding.expires_at_sec is not None:
            for task in consumers:
                builder.model.add(builder.starts[task] < binding.expires_at_sec).only_enforce_if(
                    selected
                )
        for claim in binding.lot_claims:
            lot = lots[claim.lot_id]
            assert lot.unit is not None
            unit = pool_units.setdefault(lot.spec_id, lot.unit)
            native = ScaledQuantity(value=0, scale=1, unit=lot.unit).add(claim.quantity)
            pooled = ScaledQuantity(value=0, scale=1, unit=unit).add(claim.quantity)
            by_lot[lot.lot_id].append((Fraction(native.value, native.scale), selected))
            by_pool[lot.spec_id].append((Fraction(pooled.value, pooled.scale), selected))
    supplies = {s.supply_id: s for s in problem.material_flow.supplies}
    for demand in problem.material_flow.demands:
        if (
            demand.supply_id not in by_pool
            or demand.task_id in builder.fixed
            or demand.task_id in retained_input_tasks(problem.runtime)
        ):
            continue
        supply = supplies[demand.supply_id]
        if supply.requirement.quantity is None:
            raise ValueError("库存源需求不能使用未知数值数量")
        quantity = ScaledQuantity(value=0, scale=1, unit=pool_units[demand.supply_id]).add(
            supply.requirement.quantity
        )
        amount = Fraction(quantity.value, quantity.scale) * Fraction(
            demand.share_numerator, demand.share_denominator
        )
        by_pool[demand.supply_id].append((amount, builder.performed(demand.task_id)))
    capacities = {
        lot_id: lots[lot_id].available.fraction() - lots[lot_id].reserved.fraction()
        for lot_id in by_lot
    }
    for spec, unit in pool_units.items():
        capacity = Fraction(0)
        for lot in details.lots:
            if lot.spec_id == spec and lot.quality_status == "QUALIFIED":
                available = ScaledQuantity(value=0, scale=1, unit=unit).add(
                    lot.exact(lot.available)
                )
                reserved = ScaledQuantity(value=0, scale=1, unit=unit).add(lot.exact(lot.reserved))
                capacity += Fraction(available.value, available.scale) - Fraction(
                    reserved.value, reserved.scale
                )
        capacities[spec] = capacity
    for identity, terms in (*by_lot.items(), *by_pool.items()):
        capacity = capacities[identity]
        scale = lcm(capacity.denominator, *(amount.denominator for amount, _ in terms))
        if scale > 2**40:
            raise ValueError("库存数量缩放超过安全整数范围")
        builder.model.add(
            sum(int(amount * scale) * active for amount, active in terms) <= int(capacity * scale)
        )
