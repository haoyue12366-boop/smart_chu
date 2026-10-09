"""计划预约与实物分开：发布撤销未开始分配，产出反馈再绑定实物批次。"""

from fractions import Fraction

from app.domain.candidates import stable_id
from app.domain.ids import TaskId
from app.domain.inventory import committed_fulfillments
from app.domain.quantity import ScaledQuantity, Unit
from app.domain.recovery import retained_input_tasks
from app.domain.runtime_facts import LotFact, RationalAmount
from app.domain.runtime_session import Allocation, FutureAllocation, LedgerEntry, RuntimeSession
from app.domain.scheduling_problem import SchedulingProblem


def amount_in(value: Fraction, source_unit: Unit | None, target_unit: Unit | None) -> Fraction:
    if source_unit is None and target_unit is None:
        return value
    if source_unit is None or target_unit is None:
        raise ValueError("整批份额与数值数量不能互换")
    converted = ScaledQuantity(value=0, scale=1, unit=target_unit).add(
        ScaledQuantity(value=value.numerator, scale=value.denominator, unit=source_unit)
    )
    return Fraction(converted.value, converted.scale)


def running_tasks(session: RuntimeSession) -> set[TaskId]:
    return {
        t
        for e in session.runtime.executions
        if e.status == "RUNNING"
        for t in e.task_ids
        if t not in e.completed_task_ids
    }


def reservation_entry(
    identity: str, event_id: str, lot: LotFact, before: Fraction, after: Fraction
) -> LedgerEntry:
    return LedgerEntry(
        entry_id=identity,
        event_id=event_id,
        lot_id=lot.lot_id,
        kind="RESERVE" if after > before else "RELEASE",
        before=RationalAmount.of(before),
        after=RationalAmount.of(after),
        evidence_refs=(identity,),
    )


def release_future(session: RuntimeSession, identity: str, event_id: str) -> RuntimeSession:
    """撤销仍未开始的本计划预约，保留运行中投入和累计消费。"""
    details = session.runtime.details
    assert details is not None
    protected = running_tasks(session)
    committed = {item.fulfillment_id for item in committed_fulfillments(session.runtime)}
    protected.update(
        item.task_id
        for item in session.future_allocations
        if item.inventory_fulfillment_id in committed
    )
    lots = {lot.lot_id: lot for lot in details.lots}
    entries = list(session.ledger)
    allocations = []
    for allocation in session.allocations:
        if allocation.status == "RESERVED" and allocation.task_id not in protected:
            lot = lots[allocation.lot_id]
            before = lot.reserved.fraction()
            after = before - allocation.amount.fraction() + allocation.consumed.fraction()
            if after < 0:
                raise ValueError("预约账与批次预留量不一致")
            lots[lot.lot_id] = lot.model_copy(update={"reserved": RationalAmount.of(after)})
            entries.append(
                reservation_entry(
                    identity + ":release:" + allocation.allocation_id, event_id, lot, before, after
                )
            )
            allocation = allocation.model_copy(update={"status": "CANCELLED"})
        allocations.append(allocation)
    future = tuple(
        a.model_copy(update={"status": "CANCELLED"})
        if a.task_id not in protected and a.status != "CANCELLED"
        else a
        for a in session.future_allocations
    )
    return session.model_copy(
        update={
            "ledger": tuple(entries),
            "allocations": tuple(allocations),
            "future_allocations": future,
            "runtime": session.runtime.model_copy(
                update={"details": details.model_copy(update={"lots": tuple(lots.values())})}
            ),
        }
    )


def plan_reservations(
    session: RuntimeSession,
    problem: SchedulingProblem,
    selected: set[TaskId],
    version: int,
    identity: str,
) -> RuntimeSession:
    event_id = session.runtime.event_refs[-1].root
    changed = release_future(session, identity, event_id)
    supplies = {s.supply_id: s for s in problem.material_flow.supplies}
    future = list(changed.future_allocations)
    assert changed.runtime.details is not None
    fulfilled = {
        item.supply.target_supply_id: item
        for item in changed.runtime.details.inventory_fulfillments
        if item.status != "SUPERSEDED"
    }
    for demand in problem.material_flow.demands:
        if demand.task_id not in selected or demand.task_id in retained_input_tasks(
            session.runtime
        ):
            continue
        supply = supplies[demand.supply_id]
        if any(
            item.demand_id == demand.demand_id and item.status != "CANCELLED" for item in future
        ):
            continue
        fulfillment = fulfilled.get(demand.supply_id)
        quantity = supply.requirement.exact_quantity
        amount = Fraction(demand.share_numerator, demand.share_denominator)
        if quantity is not None:
            amount *= Fraction(quantity.value, quantity.scale)
        future.append(
            FutureAllocation(
                reservation_id=stable_id("reservation", identity, demand.demand_id),
                supply_id=demand.supply_id,
                demand_id=demand.demand_id,
                task_id=demand.task_id,
                plan_version=version,
                amount=RationalAmount.of(amount),
                unit=quantity.unit if quantity else None,
                inventory_fulfillment_id=fulfillment.fulfillment_id if fulfillment else None,
                eligible_lot_ids=tuple(claim.lot_id for claim in fulfillment.supply.lot_claims)
                if fulfillment
                else (),
            )
        )
    return materialize(
        changed.model_copy(update={"future_allocations": tuple(future)}), identity, event_id
    )


def materialize(session: RuntimeSession, identity: str, event_id: str) -> RuntimeSession:
    """只为已存在且合格的库存建立实物预约；未来产物保持 PLANNED。"""
    details = session.runtime.details
    assert details is not None
    lots = {lot.lot_id: lot for lot in details.lots}
    allocations = list(session.allocations)
    entries = list(session.ledger)
    future = []
    now = session.runtime.time_origin.at(session.runtime.now_offset_sec)
    failed = {
        record.execution_id for record in session.runtime.executions if record.status == "FAILED"
    }
    for reservation in session.future_allocations:
        if reservation.status != "PLANNED":
            future.append(reservation)
            continue
        fulfillment = next(
            (
                item
                for item in details.inventory_fulfillments
                if item.fulfillment_id == reservation.inventory_fulfillment_id
                and item.status != "SUPERSEDED"
            ),
            None,
        )
        allocated = sum(
            (
                amount_in(a.amount.fraction(), lots[a.lot_id].unit, reservation.unit)
                for a in allocations
                if a.reservation_id == reservation.reservation_id and a.status != "CANCELLED"
            ),
            Fraction(0),
        )
        needed = reservation.amount.fraction() - allocated
        for lot_id in sorted(lots):
            lot = lots[lot_id]
            if needed <= 0:
                break
            if (
                (
                    lot.lot_id not in reservation.eligible_lot_ids
                    if fulfillment
                    else lot.spec_id != reservation.supply_id
                )
                or (lot.produced_by_execution_id in failed and fulfillment is None)
                or lot.quality_status != "QUALIFIED"
                or lot.produced_at > now
                or (lot.expires_at is not None and lot.expires_at <= now)
            ):
                continue
            free = lot.available.fraction() - lot.reserved.fraction()
            if fulfillment:
                claim = next(
                    item for item in fulfillment.supply.lot_claims if item.lot_id == lot_id
                )
                reserved_here = sum(
                    (
                        item.amount.fraction()
                        for item in allocations
                        if item.lot_id == lot_id
                        and item.status != "CANCELLED"
                        and any(
                            future_item.reservation_id == item.reservation_id
                            and future_item.inventory_fulfillment_id == fulfillment.fulfillment_id
                            for future_item in session.future_allocations
                        )
                    ),
                    Fraction(0),
                )
                free = min(
                    free,
                    amount_in(
                        Fraction(claim.quantity.value, claim.quantity.scale),
                        claim.quantity.unit,
                        lot.unit,
                    )
                    - reserved_here,
                )
            take = min(free, amount_in(needed, reservation.unit, lot.unit))
            if take <= 0:
                continue
            allocation_id = stable_id("allocation", reservation.reservation_id, lot.lot_id)
            existing = next((a for a in allocations if a.allocation_id == allocation_id), None)
            if existing:
                index = allocations.index(existing)
                allocations[index] = existing.model_copy(
                    update={
                        "amount": RationalAmount.of(existing.amount.fraction() + take),
                        "status": "RESERVED",
                    }
                )
            else:
                allocations.append(
                    Allocation(
                        allocation_id=allocation_id,
                        lot_id=lot.lot_id,
                        task_id=reservation.task_id,
                        plan_version=reservation.plan_version,
                        amount=RationalAmount.of(take),
                        reservation_id=reservation.reservation_id,
                    )
                )
            before = lot.reserved.fraction()
            lots[lot_id] = lot.model_copy(update={"reserved": RationalAmount.of(before + take)})
            entries.append(
                reservation_entry(
                    identity + ":reserve:" + allocation_id, event_id, lot, before, before + take
                )
            )
            needed -= amount_in(take, lot.unit, reservation.unit)
        future.append(
            reservation.model_copy(update={"status": "FULFILLED"}) if needed == 0 else reservation
        )
    return session.model_copy(
        update={
            "allocations": tuple(allocations),
            "future_allocations": tuple(future),
            "ledger": tuple(entries),
            "runtime": session.runtime.model_copy(
                update={"details": details.model_copy(update={"lots": tuple(lots.values())})}
            ),
        }
    )
