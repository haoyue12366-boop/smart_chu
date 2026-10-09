"""只读物料报告草稿；数量来自已发布计划，不能当作实际消费、产出或测量。"""

from fractions import Fraction

from app.domain.candidates import stable_id
from app.domain.events import LotMovement
from app.domain.ids import MaterialLotId, TaskId
from app.domain.quantity import ScaledQuantity
from app.domain.recovery import retained_input_tasks
from app.domain.runtime_facts import RationalAmount
from app.domain.runtime_session import RuntimeSession
from app.domain.scheduling_problem import SchedulingProblem
from app.runtime.continuous_occupation import unfinished_members
from app.runtime.material_ledger import movement_amount
from app.runtime.material_reservations import amount_in


def proposed_payload(
    session: RuntimeSession,
    problem: SchedulingProblem,
    group: tuple[TaskId, ...],
    execution_id: str,
    *,
    completed: bool,
    lot_namespace: str,
) -> dict[str, object]:
    details = session.runtime.details
    assert details is not None
    prior = next(
        (e for e in session.runtime.executions if e.execution_id.root == execution_id), None
    )
    consumed = {m.lot_id.root: m for m in prior.consumed} if prior else {}
    produced = {m.lot_id.root: m for m in prior.produced} if prior else {}
    if not completed:
        failed = {
            record.execution_id
            for record in session.runtime.executions
            if record.status == "FAILED"
        }
        for demand in problem.material_flow.demands:
            if demand.task_id not in group or demand.task_id in retained_input_tasks(
                session.runtime
            ):
                continue
            supply = next(
                s for s in problem.material_flow.supplies if s.supply_id == demand.supply_id
            )
            requirement = supply.requirement
            amount = Fraction(demand.share_numerator, demand.share_denominator)
            quantity = requirement.exact_quantity
            if quantity is not None:
                amount *= Fraction(quantity.value, quantity.scale)
            matching = [
                lot
                for lot in details.lots
                if lot.spec_id == supply.supply_id
                and lot.quality_status == "QUALIFIED"
                and lot.produced_by_execution_id not in failed
            ]
            reservation = next(
                (
                    item
                    for item in session.future_allocations
                    if item.demand_id == demand.demand_id
                    and item.status != "CANCELLED"
                    and item.inventory_fulfillment_id
                ),
                None,
            )
            if reservation:
                matching = [
                    lot
                    for lot in details.lots
                    if lot.lot_id in reservation.eligible_lot_ids
                    and lot.quality_status == "QUALIFIED"
                ]
            for lot in matching:
                if amount <= 0:
                    break
                # 同批次多个需求在本次反馈中合并，防止重复分配。
                earlier = consumed.get(lot.lot_id)
                already = movement_amount(earlier, lot) if earlier else Fraction(0)
                previous = (
                    next((m for m in prior.consumed if m.lot_id.root == lot.lot_id), None)
                    if prior
                    else None
                )
                old = movement_amount(previous, lot) if previous else Fraction(0)
                owned = sum(
                    (
                        allocation.amount.fraction() - allocation.consumed.fraction()
                        for allocation in session.allocations
                        if allocation.lot_id == lot.lot_id
                        and allocation.status == "RESERVED"
                        and allocation.task_id in group
                    ),
                    Fraction(0),
                )
                free = lot.available.fraction() - lot.reserved.fraction() + owned - (already - old)
                requirement_unit = quantity.unit if quantity else None
                take = min(free, amount_in(amount, requirement_unit, lot.unit))
                if take <= 0:
                    continue
                total = already + take
                consumed[lot.lot_id] = LotMovement(
                    lot_id=MaterialLotId(lot.lot_id),
                    spec_id=lot.spec_id,
                    quantity=lot.exact(RationalAmount.of(total))
                    if lot.quantity_kind == "EXACT"
                    else None,
                    batch_share=RationalAmount.of(total) if lot.quantity_kind != "EXACT" else None,
                )
                amount -= amount_in(take, lot.unit, requirement_unit)
            if amount:
                raise ValueError("反馈草稿缺少可用物料批次")
    else:
        for supply in problem.material_flow.supplies:
            if supply.producer_task_id not in group:
                continue
            identity = stable_id(
                lot_namespace, session.runtime.session_id.root, execution_id, supply.supply_id
            )
            quantity = supply.requirement.exact_quantity
            produced[identity] = LotMovement(
                lot_id=MaterialLotId(identity),
                spec_id=supply.supply_id,
                quantity=ScaledQuantity.model_validate(quantity.model_dump()) if quantity else None,
                batch_share=None if quantity else RationalAmount(numerator=1),
            )
    return {
        "task_id": group[0],
        "execution_id": execution_id,
        "consumed": tuple(consumed.values()),
        "produced": tuple(produced.values()) if completed else (),
        "output_status": "UNKNOWN",
        "resource_release_status": "UNCONFIRMED",
    }


def release_eligible(session: RuntimeSession, execution_id: str, group: tuple[TaskId, ...]) -> bool:
    details = session.runtime.details
    assert details is not None
    return not any(
        item.execution_id.root == execution_id
        and item.released_at is None
        and unfinished_members(session, item, group)
        for item in details.occupancies
    )
