"""Greedy 插入时核对库存实物总量和实际使用窗口。"""

from collections import defaultdict
from fractions import Fraction

from app.domain.carrier_timing import task_intervals
from app.domain.quantity import ScaledQuantity, Unit
from app.domain.recovery import retained_input_tasks
from app.domain.schedule import ScheduledAssignment
from app.domain.scheduling_problem import SchedulingProblem


def inventory_rejections(
    problem: SchedulingProblem, assignments: tuple[ScheduledAssignment, ...]
) -> tuple[str, ...]:
    details = problem.runtime.details
    if details is None:
        return ()
    selected = {item.carrier_id for item in assignments}
    carriers = [item for item in problem.inventory_supply_candidates if item.carrier_id in selected]
    if not carriers:
        return ()
    lots = {item.lot_id: item for item in details.lots}
    by_lot: dict[str, Fraction] = defaultdict(Fraction)
    by_pool: dict[str, Fraction] = defaultdict(Fraction)
    units: dict[str, Unit] = {}
    covered = {task for item in carriers for task in item.covers}
    fixed = {task for item in problem.fixed_executions for task in item.task_ids}
    fixed.update(task for item in problem.fixed_supply_fulfillments for task in item.task_ids)
    fixed.update(task for item in problem.advance_preparations for task in item.task_ids)
    fixed.update(retained_input_tasks(problem.runtime))
    ports = task_intervals(problem, assignments)
    reasons = []
    for carrier in carriers:
        binding = carrier.inventory_supply
        assert binding is not None
        for claim in binding.lot_claims:
            lot = lots[claim.lot_id]
            assert lot.unit is not None
            unit = units.setdefault(lot.spec_id, lot.unit)
            native = ScaledQuantity(value=0, scale=1, unit=lot.unit).add(claim.quantity)
            pooled = ScaledQuantity(value=0, scale=1, unit=unit).add(claim.quantity)
            by_lot[lot.lot_id] += Fraction(native.value, native.scale)
            by_pool[lot.spec_id] += Fraction(pooled.value, pooled.scale)
        if any(ports[task].start_sec < binding.available_at_sec for task in carrier.covers):
            reasons.append("库存尚未形成")
        for demand in problem.material_flow.demands:
            port = ports.get(demand.task_id)
            if (
                demand.supply_id == binding.target_supply_id
                and port
                and binding.expires_at_sec is not None
                and port.start_sec >= binding.expires_at_sec
            ):
                reasons.append("使用时库存已失效")
    for identity, used in by_lot.items():
        if used > lots[identity].available.fraction() - lots[identity].reserved.fraction():
            reasons.append("库存批次数量被重复承诺")
    supplies = {item.supply_id: item for item in problem.material_flow.supplies}
    for demand in problem.material_flow.demands:
        if demand.supply_id in units and demand.task_id not in fixed | covered:
            quantity = supplies[demand.supply_id].requirement.quantity
            if quantity is None:
                reasons.append("库存源需求缺少精确数量")
                continue
            amount = ScaledQuantity(value=0, scale=1, unit=units[demand.supply_id]).add(quantity)
            by_pool[demand.supply_id] += Fraction(amount.value, amount.scale) * Fraction(
                demand.share_numerator, demand.share_denominator
            )
    for identity, used in by_pool.items():
        capacity = Fraction(0)
        for lot in details.lots:
            if lot.spec_id == identity and lot.quality_status == "QUALIFIED":
                zero = ScaledQuantity(value=0, scale=1, unit=units[identity])
                free, reserved = (
                    zero.add(lot.exact(lot.available)),
                    zero.add(lot.exact(lot.reserved)),
                )
                capacity += Fraction(free.value, free.scale) - Fraction(
                    reserved.value, reserved.scale
                )
        if used > capacity:
            reasons.append("库存替代与原菜单需求超过实际余量")
    return tuple(dict.fromkeys(reasons))
