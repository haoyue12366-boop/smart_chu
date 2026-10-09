"""重新从源供应和需求推导计划分配，拒绝IR数量篡改。"""

from collections import Counter
from fractions import Fraction

from app.domain.ids import CarrierId, TaskId
from app.validation.knowledge_materials import _share
from app.validation.schedule_context import Scan


def check_planned_allocations(scan: Scan) -> None:
    problem = scan.problem
    if not (
        problem.shared_prep_candidates
        or problem.thermal_batch_candidates
        or problem.material_allocations.allocations
        or problem.material_allocations.remainders
    ):
        return
    supplies = {s.supply_id: s for s in problem.material_flow.supplies}
    expected: Counter[tuple[CarrierId, str, str, TaskId, Fraction, bool]] = Counter()
    remainder: Counter[tuple[CarrierId, str, Fraction, bool]] = Counter()
    for carrier in (
        *problem.standalone_candidates,
        *problem.shared_prep_candidates,
        *problem.thermal_batch_candidates,
        *problem.inventory_supply_candidates,
    ):
        for output in carrier.material_outputs:
            supply = supplies.get(output.spec_id)
            if supply is None or supply.producer_task_id not in carrier.covers:
                scan.fail("PLANNED_MATERIAL", "载体产出没有对应的源生产者", carrier.carrier_id.root)
                continue
            total = Fraction(0)
            for demand in problem.material_flow.demands:
                if demand.supply_id != supply.supply_id:
                    continue
                try:
                    share = _share(demand.requirement, supply.requirement)
                except (ValueError, ZeroDivisionError) as exc:
                    scan.fail("PLANNED_MATERIAL", str(exc), demand.demand_id)
                    continue
                total += share
                expected[
                    (
                        carrier.carrier_id,
                        supply.supply_id,
                        demand.demand_id,
                        demand.task_id,
                        share,
                        demand.is_loss,
                    )
                ] += 1
            if total > 1:
                scan.fail("PLANNED_MATERIAL", "分配和损耗超过源净产量", supply.supply_id)
            remainder[(carrier.carrier_id, supply.supply_id, 1 - total, False)] += 1
    actual = Counter(
        (
            a.producer_carrier_id,
            a.supply_id,
            a.demand_id,
            a.consumer_task_id,
            Fraction(a.share_numerator, a.share_denominator),
            a.is_loss,
        )
        for a in problem.material_allocations.allocations
    )
    actual_remaining = Counter(
        (
            r.producer_carrier_id,
            r.supply_id,
            Fraction(r.share_numerator, r.share_denominator),
            r.is_actual_inventory,
        )
        for r in problem.material_allocations.remainders
    )
    if actual != expected or actual_remaining != remainder:
        scan.fail("PLANNED_MATERIAL", "计划分配或余量偏离逐源数量；不能用未选中载体的产出供料")
