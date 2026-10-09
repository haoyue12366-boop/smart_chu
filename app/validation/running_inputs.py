"""独立从源工序、累计消费与当前实物核对冻结执行的未投入数量。"""

from collections import defaultdict
from fractions import Fraction

from app.domain.candidates import stable_id
from app.domain.canonical_recipe import CanonicalRecipeModel, OperationTemplate
from app.domain.ids import TaskId
from app.domain.inventory import committed_fulfillments
from app.domain.material import MaterialRequirement
from app.domain.quantity import ScaledQuantity, Unit
from app.validation.knowledge_materials import _share
from app.validation.schedule_context import Scan


def _convert(value: Fraction, source: Unit | None, target: Unit | None) -> Fraction:
    if source is None and target is None:
        return value
    if source is None or target is None:
        raise ValueError("整批份额不能换算成精确单位")
    quantity = ScaledQuantity(value=0, scale=1, unit=target).add(
        ScaledQuantity(value=value.numerator, scale=value.denominator, unit=source)
    )
    return Fraction(quantity.value, quantity.scale)


def _sources(scan: Scan) -> dict[TaskId, tuple[str, CanonicalRecipeModel, OperationTemplate]]:
    recipes = {item.recipe_id: item for item in scan.knowledge.recipes}
    result = {
        task.task_id: (
            task.recipe_instance_id.root,
            recipes[instance.recipe_id],
            scan.operations[task.task_id],
        )
        for instance in scan.problem.recipe_instances
        if instance.recipe_id in recipes
        for task in scan.problem.logical_tasks
        if task.recipe_instance_id == instance.recipe_instance_id
        and task.task_id in scan.operations
    }
    details = scan.runtime.details
    assert details is not None
    retired = set(details.retired_task_ids)
    if retired:
        for instance_id in details.cancelled_instance_ids:
            for recipe in scan.knowledge.recipes:
                for operation in recipe.operations:
                    identity = TaskId(stable_id("task", instance_id, operation.operation_id.root))
                    if identity in retired:
                        result[identity] = (instance_id, recipe, operation)
    return result


def check_running_materials(scan: Scan, future: dict[str, Fraction]) -> None:
    details = scan.runtime.details
    if details is None:
        return
    running = tuple(item for item in scan.runtime.executions if item.status == "RUNNING")
    if not running:
        return
    sources = _sources(scan)
    lots = {item.lot_id: item for item in details.lots}
    now = scan.runtime.time_origin.at(scan.runtime.now_offset_sec)
    capacities = {
        lot.lot_id: lot.available.fraction()
        for lot in details.lots
        if lot.quality_status == "QUALIFIED"
        and lot.produced_at <= now
        and (lot.expires_at is None or lot.expires_at > now)
    }
    obligations: dict[tuple[str, Unit | None, tuple[str, ...]], Fraction] = defaultdict(Fraction)
    fulfills = {item.supply.target_supply_id: item for item in committed_fulfillments(scan.runtime)}
    for execution in running:
        if execution.recovery_kind == "RESUME":
            continue  # 续做的完整保留投入由恢复证据扫描独立核对。
        expected: dict[tuple[str, Unit | None], Fraction] = defaultdict(Fraction)
        for task_id in execution.started_task_ids:
            original = sources.get(task_id)
            if original is None:
                scan.fail("RUNNING_MATERIAL_STOCK", "运行阶段缺少原工艺投入依据", task_id.root)
                continue
            instance_id, recipe, operation = original
            supplies = {item.spec_id: item for item in recipe.ingredient_requirements}
            supplies.update(
                (item.spec_id, item) for op in recipe.operations for item in op.material_outputs
            )
            requirements = (
                *operation.material_inputs,
                *(
                    MaterialRequirement(
                        requirement_id=f"loss-{index}",
                        spec_id=loss.spec_id,
                        quantity_kind="EXACT",
                        quantity=loss.quantity,
                        provenance_refs=loss.provenance_refs,
                    )
                    for index, loss in enumerate(operation.losses)
                ),
            )
            for requirement in requirements:
                source = supplies.get(requirement.spec_id)
                if source is None:
                    scan.fail("RUNNING_MATERIAL_STOCK", "原工艺投入缺少来源", task_id.root)
                    continue
                try:
                    amount = _share(requirement, source)
                    unit = source.quantity.unit if source.quantity else None
                    if source.quantity:
                        amount *= Fraction(source.quantity.value, source.quantity.scale)
                    expected[(stable_id("material", instance_id, source.spec_id), unit)] += amount
                except ValueError as exc:
                    scan.fail("RUNNING_MATERIAL_STOCK", str(exc), task_id.root)
        for (spec, unit), required in expected.items():
            fulfillment = fulfills.get(spec)
            eligible = (
                tuple(sorted(claim.lot_id for claim in fulfillment.supply.lot_claims))
                if fulfillment
                else tuple(sorted(lot.lot_id for lot in details.lots if lot.spec_id == spec))
            )
            try:
                actual = sum(
                    (
                        _convert(
                            Fraction(movement.quantity.value, movement.quantity.scale)
                            if movement.quantity
                            else movement.batch_share.fraction()
                            if movement.batch_share
                            else Fraction(0),
                            movement.quantity.unit if movement.quantity else None,
                            unit,
                        )
                        for movement in execution.consumed
                        if movement.lot_id.root in eligible
                    ),
                    Fraction(0),
                )
                obligations[(spec, unit, eligible)] += max(Fraction(0), required - actual)
            except ValueError as exc:
                scan.fail("RUNNING_MATERIAL_STOCK", str(exc), execution.execution_id.root)
    # 若盘点撤销了预约，未来任务仍不能再次承诺运行中尚欠的同一份实物。
    modeled_supplies = {item.supply_id: item for item in scan.problem.material_flow.supplies}
    for spec, share in future.items():
        if not share or not any(key[0] == spec for key in obligations):
            continue
        supply = modeled_supplies[spec]
        unit = supply.requirement.quantity.unit if supply.requirement.quantity else None
        amount = (
            share * Fraction(supply.requirement.quantity.value, supply.requirement.quantity.scale)
            if supply.requirement.quantity
            else share
        )
        key = next(key for key in obligations if key[0] == spec)
        obligations[(spec, unit, key[2])] += amount
    for (spec, unit, eligible), needed in sorted(
        obligations.items(), key=lambda item: len(item[0][2])
    ):
        try:
            for identity in eligible:
                if needed <= 0:
                    break
                free = capacities.get(identity, Fraction(0))
                take = min(free, _convert(needed, unit, lots[identity].unit))
                capacities[identity] = free - take
                needed -= _convert(take, lots[identity].unit, unit)
            if needed > 0:
                scan.fail("RUNNING_MATERIAL_STOCK", "冻结执行的剩余投入超过合格实物", spec)
        except ValueError as exc:
            scan.fail("RUNNING_MATERIAL_STOCK", str(exc), spec)
