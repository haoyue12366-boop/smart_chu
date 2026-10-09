"""菜单录入时明确声明已备好的前处理；不生成执行完成事件。"""

import re
from collections import defaultdict
from fractions import Fraction

from app.domain.advance_preparation import AdvancePreparation, AdvancePreparationRule
from app.domain.base import content_hash
from app.domain.candidates import stable_id
from app.domain.canonical_recipe import CanonicalRecipeModel
from app.domain.events import RuntimeEvent
from app.domain.ids import OperationId
from app.domain.material import MaterialRequirement
from app.domain.policy import SchedulingPolicy
from app.domain.runtime_facts import LotFact, RationalAmount
from app.domain.scheduling_problem import RecipeInstance


def checked_rules(
    recipe: CanonicalRecipeModel, policy: SchedulingPolicy
) -> tuple[AdvancePreparationRule, ...]:
    if policy.advance_preparation_mode != "ASSUME_READY":
        return ()
    rules = tuple(
        rule for rule in policy.advance_preparation_rules if rule.recipe_id == recipe.recipe_id
    )
    seen: set[OperationId] = set()
    operations = {op.operation_id: op for op in recipe.operations}
    for rule in rules:
        scope = set(rule.operation_ids)
        if content_hash(recipe) != rule.recipe_hash or not rule.evidence_refs:
            raise ValueError("提前备料规则与固定菜谱哈希或来源不符")
        if (
            not scope
            or len(scope) != len(rule.operation_ids)
            or not scope <= operations.keys()
            or seen & scope
        ):
            raise ValueError("提前备料工序缺失、重复或重叠")
        seen.update(scope)
        if any(
            dep.successor_id in scope and dep.predecessor_id not in scope
            for dep in recipe.dependencies
        ):
            raise ValueError("提前备料遗漏前置工艺，不能跳过中途等待")
        if not any(
            operations[op].action in {"MARINATE", "WAIT", "CHILL", "FREEZE"} for op in scope
        ):
            raise ValueError("提前备料规则没有明确准备过程")
        for op_id in scope:
            op = operations[op_id]
            if op.action in {"HEAT", "PREHEAT", "UNLOAD", "FINISH", "UNKNOWN", "STIR"}:
                raise ValueError("提前备料闭包含热加工或成菜工艺")
            if any(
                use.resource_type == "DEVICE" and use.physical_resource_id != "fridge_1"
                for use in op.resource_requirements
            ):
                raise ValueError("提前备料闭包含必须调度的厨具")
            if op.execution_policy.fixed_batch_id or op.execution_policy.thermal_group_id:
                raise ValueError("提前备料不得截断固定设备程序")
        if any(
            dep.predecessor_id in scope
            and dep.successor_id not in scope
            and dep.max_lag_sec is not None
            for dep in recipe.dependencies
        ):
            raise ValueError("提前备料缺少跨开工边界的有限保存时间依据")
    return rules


def _share(demand: MaterialRequirement, supply: MaterialRequirement) -> Fraction:
    if demand.quantity is not None and supply.quantity is not None:
        converted = supply.quantity.model_copy(update={"value": 0}).add(demand.quantity)
        return Fraction(converted.value, converted.scale) / Fraction(
            supply.quantity.value, supply.quantity.scale
        )
    match = re.search(
        r"(?:原配方份额|本物料整批的)(\d+(?:/\d+)?)", demand.qualitative_quantity or ""
    )
    if demand.quantity is None and supply.quantity is None and match:
        return Fraction(match[1])
    raise ValueError("提前备料缺少可追溯数量或整批份额")


def declare_preparations(
    recipe: CanonicalRecipeModel,
    instance: RecipeInstance,
    policy: SchedulingPolicy,
    event: RuntimeEvent,
    available_at_sec: int,
    raw_lots: tuple[LotFact, ...],
) -> tuple[tuple[AdvancePreparation, ...], tuple[LotFact, ...]]:
    rules = checked_rules(recipe, policy)
    if not rules:
        return (), raw_lots
    operations = {op.operation_id: op for op in recipe.operations}
    scope = {op for rule in rules for op in rule.operation_ids}
    supplies = {item.spec_id: item for item in recipe.ingredient_requirements}
    supplies.update(
        (item.spec_id, item) for op in recipe.operations for item in op.material_outputs
    )
    consumed: dict[str, Fraction] = defaultdict(Fraction)
    for op_id in scope:
        op = operations[op_id]
        for demand in op.material_inputs:
            consumed[demand.spec_id] += _share(demand, supplies[demand.spec_id])
        for loss in op.losses:
            supply = supplies[loss.spec_id]
            consumed[loss.spec_id] += _share(
                MaterialRequirement(
                    requirement_id="loss",
                    spec_id=loss.spec_id,
                    quantity_kind="EXACT",
                    quantity=loss.quantity,
                ),
                supply,
            )
    if any(value > 1 for value in consumed.values()):
        raise ValueError("提前备料消耗超过原配方供应")
    declarations = []
    lots = []
    for lot in raw_lots:
        remaining = lot.produced.fraction() * (1 - consumed[lot.source_spec_id])
        lots.append(
            lot.model_copy(
                update={
                    "produced": RationalAmount.of(remaining),
                    "available": RationalAmount.of(remaining),
                }
            )
        )
    specs = {item.spec_id: item for item in recipe.material_specs}
    for rule in rules:
        identity = stable_id("advance-preparation", instance.recipe_instance_id.root, rule.rule_id)
        declaration = AdvancePreparation(
            preparation_id=identity,
            rule_id=rule.rule_id,
            recipe_instance_id=instance.recipe_instance_id,
            recipe_id=recipe.recipe_id,
            task_ids=tuple(
                stable_id("task", instance.recipe_instance_id.root, op.root)
                for op in rule.operation_ids
            ),
            operation_ids=rule.operation_ids,
            available_at_sec=available_at_sec,
            original_duration_sec=sum(
                operations[op].duration.execution_sec or 0 for op in rule.operation_ids
            ),
            evidence_refs=("USER_POLICY_ASSUMPTION", event.event_id.root, *rule.evidence_refs),
            description=rule.description,
        )
        declarations.append(declaration)
        for op_id in rule.operation_ids:
            for output in operations[op_id].material_outputs:
                share = 1 - consumed[output.spec_id]
                if share <= 0:
                    continue
                quantity = output.exact_quantity
                amount = share * (Fraction(quantity.value, quantity.scale) if quantity else 1)
                supply_id = stable_id("material", instance.recipe_instance_id.root, output.spec_id)
                lots.append(
                    LotFact(
                        lot_id=stable_id("prepared-input", identity, output.spec_id),
                        spec_id=supply_id,
                        source_spec_id=output.spec_id,
                        preparation_id=identity,
                        quantity_kind="EXACT" if quantity else "QUALITATIVE",
                        unit=quantity.unit if quantity else None,
                        produced=RationalAmount.of(amount),
                        available=RationalAmount.of(amount),
                        quality_status="QUALIFIED",
                        produced_at=event.occurred_at,
                        source_event_id=event.event_id.root,
                        availability_evidence=declaration.evidence_refs,
                        storage_state="assumed_prepared_by_user_policy",
                        material_spec=specs.get(output.spec_id),
                    )
                )
    return tuple(declarations), tuple(lots)
