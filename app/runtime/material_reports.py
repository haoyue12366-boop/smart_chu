"""核对执行报告与已发布完整需求；不把缺失测量补成默认消费或产出。"""

from collections import defaultdict
from fractions import Fraction

from app.domain.candidates import stable_id
from app.domain.events import ExecutionPayload, LotMovement, RuntimeEvent
from app.domain.knowledge import MenuKnowledgeView
from app.domain.material import MaterialSpec
from app.domain.quantity import Unit
from app.domain.runtime_session import RuntimeSession
from app.runtime.material_ledger import movement_amount
from app.runtime.material_reservations import amount_in


def validate_reports(
    session: RuntimeSession, event: RuntimeEvent, knowledge: MenuKnowledgeView
) -> None:
    payload = event.payload
    assert isinstance(payload, ExecutionPayload)
    binding = next((b for b in session.bindings if payload.task_id in b.assignment.task_ids), None)
    if binding is None:
        return  # 执行状态机报告缺少绑定。
    details = session.runtime.details
    assert details is not None
    previous = next(
        (e for e in session.runtime.executions if e.execution_id == payload.execution_id), None
    )
    span = next(p.interval for p in binding.task_spans if p.task_id == payload.task_id)
    group = {p.task_id for p in binding.task_spans if p.interval == span}
    started = group | (set(previous.started_task_ids) if previous else set())
    expected: dict[str, Fraction] = defaultdict(Fraction)
    units: dict[str, Unit | None] = {}
    lots = {lot.lot_id: lot for lot in details.lots}
    inventory_keys: dict[str, str] = {}
    for reservation in session.future_allocations:
        if reservation.task_id in started and (
            reservation.plan_version == binding.plan_version
            or (
                reservation.inventory_fulfillment_id is not None
                and reservation.status != "CANCELLED"
            )
        ):
            if reservation.inventory_fulfillment_id:
                if reservation.status != "FULFILLED":
                    raise ValueError("库存供应预约未完整绑定实物")
                for allocation in session.allocations:
                    if (
                        allocation.reservation_id != reservation.reservation_id
                        or allocation.status == "CANCELLED"
                    ):
                        continue
                    key = "lot:" + allocation.lot_id
                    inventory_keys[allocation.lot_id] = key
                    expected[key] += allocation.amount.fraction()
                    units[key] = lots[allocation.lot_id].unit
                continue
            expected[reservation.supply_id] += reservation.amount.fraction()
            units[reservation.supply_id] = reservation.unit
    consumed = {m.lot_id: m for m in previous.consumed} if previous else {}
    consumed.update({m.lot_id: m for m in payload.consumed})
    actual: dict[str, Fraction] = defaultdict(Fraction)
    for movement in consumed.values():
        lot = lots.get(movement.lot_id.root)
        key = inventory_keys.get(movement.lot_id.root, movement.spec_id)
        if lot is None or movement.spec_id != lot.spec_id or key not in expected:
            raise ValueError("消费报告引用当前执行需求之外的物料")
        actual[key] += amount_in(movement_amount(movement, lot), lot.unit, units[key])
    if any(actual[spec] > amount for spec, amount in expected.items()):
        raise ValueError("累计消费超过已发布执行需求")
    if event.event_type == "OPERATION_COMPLETED" and any(
        actual[spec] != amount for spec, amount in expected.items()
    ):
        raise ValueError("完成反馈缺少完整实际消费报告")
    completed = group | (set(previous.completed_task_ids) if previous else set())
    output_requirements: dict[str, tuple[Fraction, Unit | None]] = {}
    recipes = {recipe.recipe_id: recipe for recipe in knowledge.recipes}
    for instance in session.menu:
        for operation in recipes[instance.recipe_id].operations:
            task = stable_id("task", instance.recipe_instance_id.root, operation.operation_id.root)
            if task not in {t.root for t in completed}:
                continue
            for requirement in operation.material_outputs:
                quantity = requirement.exact_quantity
                output_requirements[
                    stable_id("material", instance.recipe_instance_id.root, requirement.spec_id)
                ] = (
                    Fraction(quantity.value, quantity.scale) if quantity else Fraction(1),
                    quantity.unit if quantity else None,
                )
    produced: dict[object, LotMovement] = (
        {m.lot_id: m for m in previous.produced} if previous else {}
    )
    produced.update({m.lot_id: m for m in payload.produced})
    reported: dict[str, Fraction] = defaultdict(Fraction)
    for movement in produced.values():
        if movement.spec_id not in output_requirements:
            raise ValueError("产出报告不属于已结束工序的物料端口")
        _, unit = output_requirements[movement.spec_id]
        quantity = movement.quantity
        amount = (
            Fraction(quantity.value, quantity.scale)
            if quantity
            else movement.batch_share.fraction()
            if movement.batch_share
            else Fraction(0)
        )
        reported[movement.spec_id] += amount_in(amount, quantity.unit if quantity else None, unit)
    if event.event_type == "OPERATION_COMPLETED" and any(
        reported[spec] != amount for spec, (amount, _) in output_requirements.items()
    ):
        raise ValueError("完成反馈缺少完整实际产出报告")


def source_specs(session: RuntimeSession, knowledge: MenuKnowledgeView) -> dict[str, MaterialSpec]:
    recipes = {recipe.recipe_id: recipe for recipe in knowledge.recipes}
    return {
        stable_id("material", instance.recipe_instance_id.root, spec.spec_id): spec
        for instance in session.menu
        for spec in recipes[instance.recipe_id].material_specs
    }
