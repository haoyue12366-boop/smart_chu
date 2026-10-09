"""追加式实际物料账；报告中的消费、产出是本次执行累计数量。"""

from fractions import Fraction
from typing import Literal

from app.domain.events import ExecutionPayload, LotMovement, MaterialPayload, RuntimeEvent
from app.domain.material import MaterialSpec
from app.domain.runtime_facts import LotFact, RationalAmount
from app.domain.runtime_session import LedgerEntry, RuntimeSession
from app.domain.runtime_snapshot import ExecutionRecord


def movement_amount(movement: LotMovement, lot: LotFact) -> Fraction:
    if lot.quantity_kind != "EXACT":
        if movement.batch_share is None or movement.quantity is not None:
            raise ValueError("定性物料只能按有来源的原配方份额记账")
        return movement.batch_share.fraction()
    if movement.quantity is None or lot.unit is None:
        raise ValueError("精确物料必须有匹配单位的数量")
    converted = lot.exact(RationalAmount(numerator=0)).add(movement.quantity)
    return Fraction(converted.value, converted.scale)


def apply_material_effects(
    event: RuntimeEvent,
    session: RuntimeSession,
    previous: ExecutionRecord | None = None,
    source_specs: dict[str, MaterialSpec] | None = None,
) -> RuntimeSession:
    details = session.runtime.details
    if details is None:
        raise ValueError("运行物料账缺少版本化事实")
    lots = {lot.lot_id: lot for lot in details.lots}
    entries = list(session.ledger)
    allocations = list(session.allocations)
    future_allocations = session.future_allocations
    payload = event.payload

    def append(
        lot: LotFact,
        kind: Literal["CONSUME", "PRODUCE", "LOSS", "ADJUST", "RELEASE"],
        before: Fraction,
        after: Fraction,
    ) -> None:
        entries.append(
            LedgerEntry(
                entry_id=f"{event.event_id.root}:{kind}:{lot.lot_id}",
                event_id=event.event_id.root,
                execution_id=payload.execution_id.root
                if isinstance(payload, ExecutionPayload)
                else None,
                lot_id=lot.lot_id,
                kind=kind,
                before=RationalAmount.of(before),
                after=RationalAmount.of(after),
                evidence_refs=(event.event_id.root,),
            )
        )

    if isinstance(payload, MaterialPayload):
        lot = lots.get(payload.lot_id.root)
        if lot is None:
            raise ValueError("盘点必须引用已有批次")
        before = movement_amount(
            LotMovement(lot_id=payload.lot_id, spec_id=lot.spec_id, quantity=payload.before), lot
        )
        after = movement_amount(
            LotMovement(lot_id=payload.lot_id, spec_id=lot.spec_id, quantity=payload.after), lot
        )
        if before != lot.available.fraction() or not payload.evidence_refs:
            raise ValueError("盘点前值不符或缺少证据")
        lots[lot.lot_id] = lot.model_copy(
            update={
                "available": RationalAmount.of(after),
                "reserved": RationalAmount(numerator=0),
            }
        )
        append(lot, "ADJUST", before, after)
        if lot.reserved.fraction():
            append(lot, "RELEASE", lot.reserved.fraction(), Fraction(0))
        allocations = [
            a.model_copy(update={"status": "CANCELLED"})
            if a.lot_id == lot.lot_id and a.status == "RESERVED"
            else a
            for a in allocations
        ]
        future_allocations = tuple(
            a.model_copy(update={"status": "CANCELLED"}) if a.supply_id == lot.spec_id else a
            for a in future_allocations
        )
    elif isinstance(payload, ExecutionPayload):
        owned_tasks = next(
            (
                set(b.assignment.task_ids)
                for b in session.bindings
                if payload.task_id in b.assignment.task_ids
            ),
            {payload.task_id},
        )
        old_consumed = {m.lot_id.root: m for m in previous.consumed} if previous else {}
        old_produced = {m.lot_id.root: m for m in previous.produced} if previous else {}
        for values in (payload.consumed, payload.produced):
            if len({m.lot_id for m in values}) != len(values):
                raise ValueError("同一报告批次重复")
        for movement in payload.consumed:
            lot = lots.get(movement.lot_id.root)
            if lot is None or lot.spec_id != movement.spec_id:
                raise ValueError("实际消费引用不存在或未合格批次")
            before_report = old_consumed.get(lot.lot_id)
            delta = movement_amount(movement, lot) - (
                movement_amount(before_report, lot) if before_report else 0
            )
            if delta < 0 or delta > lot.available.fraction():
                raise ValueError("累计消费倒退或超过真实余量")
            if delta:
                if lot.quality_status != "QUALIFIED":
                    raise ValueError("新增消费只能使用合格批次")
                linked = [
                    item
                    for item in session.future_allocations
                    if item.inventory_fulfillment_id
                    and item.task_id in owned_tasks
                    and lot.lot_id in item.eligible_lot_ids
                    and item.status != "CANCELLED"
                ]
                approved = [
                    item
                    for item in details.inventory_fulfillments
                    if item.status != "SUPERSEDED"
                    and any(
                        reservation.inventory_fulfillment_id == item.fulfillment_id
                        for reservation in linked
                    )
                ]
                if (
                    any(
                        record.execution_id == lot.produced_by_execution_id
                        and record.status == "FAILED"
                        for record in session.runtime.executions
                    )
                    and not approved
                ):
                    raise ValueError("失败尝试的余料须有明确的库存替代依据")
                if lot.produced_at > event.occurred_at or (
                    lot.expires_at and lot.expires_at <= event.occurred_at
                ):
                    raise ValueError("消费时物料尚未形成或已失效")
                if any(
                    item.supply.expires_at_sec is None
                    or session.runtime.time_origin.offset(event.occurred_at)
                    >= item.supply.expires_at_sec
                    for item in approved
                ):
                    raise ValueError("实际消费超过库存替代规则的审核使用期限")
                owned = sum(
                    (
                        a.amount.fraction() - a.consumed.fraction()
                        for a in allocations
                        if a.lot_id == lot.lot_id
                        and a.status == "RESERVED"
                        and a.task_id in owned_tasks
                    ),
                    Fraction(0),
                )
                if delta > lot.available.fraction() - lot.reserved.fraction() + owned:
                    raise ValueError("实际消费侵占其他执行的物料预约")
                remaining = delta
                for index, allocation in enumerate(allocations):
                    if remaining <= 0:
                        break
                    if (
                        allocation.lot_id != lot.lot_id
                        or allocation.status != "RESERVED"
                        or allocation.task_id not in owned_tasks
                    ):
                        continue
                    take = min(
                        remaining, allocation.amount.fraction() - allocation.consumed.fraction()
                    )
                    consumed_amount = allocation.consumed.fraction() + take
                    allocations[index] = allocation.model_copy(
                        update={
                            "consumed": RationalAmount.of(consumed_amount),
                            "status": "CONSUMED"
                            if consumed_amount == allocation.amount.fraction()
                            else "RESERVED",
                        }
                    )
                    remaining -= take
                released = delta - remaining
                before = lot.available.fraction()
                lots[lot.lot_id] = lot.model_copy(
                    update={
                        "available": RationalAmount.of(before - delta),
                        "reserved": RationalAmount.of(lot.reserved.fraction() - released),
                    }
                )
                append(lot, "CONSUME", before, before - delta)
                if released:
                    append(
                        lot, "RELEASE", lot.reserved.fraction(), lot.reserved.fraction() - released
                    )
        for movement in payload.produced:
            if event.event_type not in {"OPERATION_COMPLETED", "OPERATION_FAILED"}:
                raise ValueError("产物必须有实际结束事件")
            lot = lots.get(movement.lot_id.root)
            if lot is None:
                quantity = movement.quantity
                amount = (
                    RationalAmount(numerator=quantity.value, denominator=quantity.scale)
                    if quantity
                    else movement.batch_share
                )
                if amount is None:
                    raise ValueError("产物数量未知")
                lot = LotFact(
                    lot_id=movement.lot_id.root,
                    spec_id=movement.spec_id,
                    source_spec_id=source_specs[movement.spec_id].spec_id
                    if source_specs and movement.spec_id in source_specs
                    else movement.spec_id,
                    material_spec=(source_specs or {}).get(movement.spec_id),
                    quantity_kind="EXACT" if quantity else "QUALITATIVE",
                    unit=quantity.unit if quantity else None,
                    produced=RationalAmount(numerator=0),
                    available=RationalAmount(numerator=0),
                    quality_status=payload.output_status,
                    produced_at=event.occurred_at,
                    source_event_id=event.event_id.root,
                    availability_evidence=(event.event_id.root,),
                    produced_by_execution_id=payload.execution_id,
                )
            if (
                lot.produced_by_execution_id != payload.execution_id
                or lot.spec_id != movement.spec_id
            ):
                raise ValueError("产出批次身份冲突")
            previous_report = old_produced.get(lot.lot_id)
            cumulative = movement_amount(movement, lot)
            delta = cumulative - (movement_amount(previous_report, lot) if previous_report else 0)
            if delta < 0:
                raise ValueError("累计产出不能回退")
            available = (
                lot.available.fraction() + delta
                if payload.output_status == "QUALIFIED"
                else Fraction(0)
            )
            lots[lot.lot_id] = lot.model_copy(
                update={
                    "produced": RationalAmount.of(cumulative),
                    "available": RationalAmount.of(available),
                    "quality_status": payload.output_status,
                }
            )
            if delta or lot.quality_status != payload.output_status:
                append(
                    lot,
                    "PRODUCE" if payload.output_status == "QUALIFIED" else "LOSS",
                    lot.available.fraction(),
                    available,
                )
    return session.model_copy(
        update={
            "ledger": tuple(entries),
            "allocations": tuple(allocations),
            "future_allocations": future_allocations,
            "runtime": session.runtime.model_copy(
                update={"details": details.model_copy(update={"lots": tuple(lots.values())})}
            ),
        }
    )
