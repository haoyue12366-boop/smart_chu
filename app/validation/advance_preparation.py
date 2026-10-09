"""独立从固定菜谱和策略核对前置准备，不依赖编译器的选择或闭包实现。"""

import re
from collections import Counter, defaultdict
from fractions import Fraction

from app.domain.base import content_hash
from app.domain.candidates import stable_id
from app.domain.ids import TaskId
from app.domain.material import MaterialRequirement
from app.validation.schedule_context import Scan


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
    raise ValueError("准备物料份额无依据")


def check_advance_preparations(scan: Scan) -> None:
    problem = scan.problem
    if not problem.advance_preparations:
        if scan.runtime.details is not None and any(
            lot.preparation_id for lot in scan.runtime.details.lots
        ):
            active_instances = {item.recipe_instance_id for item in problem.recipe_instances}
            if any(
                item.recipe_instance_id in active_instances
                for item in scan.runtime.details.advance_preparations
            ):
                scan.fail("PREPARATION_SOURCE", "提前备料供应缺少问题声明")
        return
    if problem.policy.advance_preparation_mode != "ASSUME_READY":
        scan.fail("PREPARATION_SOURCE", "当前策略未授权提前备料")
    rules = {rule.rule_id: rule for rule in problem.policy.advance_preparation_rules}
    recipes = {recipe.recipe_id: recipe for recipe in scan.knowledge.recipes}
    instances = {item.recipe_instance_id: item for item in problem.recipe_instances}
    seen: set[TaskId] = set()
    for preparation in problem.advance_preparations:
        rule = rules.get(preparation.rule_id)
        instance = instances.get(preparation.recipe_instance_id)
        recipe = recipes.get(preparation.recipe_id)
        if (
            rule is None
            or recipe is None
            or instance is None
            or instance.recipe_id != preparation.recipe_id
        ):
            scan.fail("PREPARATION_SOURCE", "提前备料不属于明确的策略或菜谱实例")
            continue
        operations = {op.operation_id: op for op in recipe.operations}
        scope = set(preparation.operation_ids)
        expected_tasks = tuple(
            stable_id("task", instance.recipe_instance_id.root, op.root)
            for op in rule.operation_ids
        )
        if (
            rule.recipe_id != recipe.recipe_id
            or rule.recipe_hash != content_hash(recipe)
            or preparation.operation_ids != rule.operation_ids
            or not scope
            or len(scope) != len(preparation.operation_ids)
            or not scope <= operations.keys()
            or tuple(task.root for task in preparation.task_ids) != expected_tasks
            or preparation.preparation_id
            != stable_id("advance-preparation", instance.recipe_instance_id.root, rule.rule_id)
            or preparation.description != rule.description
            or not rule.evidence_refs
            or not set(rule.evidence_refs) <= set(preparation.evidence_refs)
            or "USER_POLICY_ASSUMPTION" not in preparation.evidence_refs
            or preparation.available_at_sec > scan.runtime.now_offset_sec
            or seen.intersection(preparation.task_ids)
        ):
            scan.fail(
                "PREPARATION_SOURCE", "提前备料身份、原文或授权来源不符", preparation.preparation_id
            )
            continue
        seen.update(preparation.task_ids)
        if preparation.original_duration_sec != sum(
            operations[op].duration.execution_sec or 0 for op in scope
        ):
            scan.fail("PREPARATION_DURATION", "提前准备清单修改了原始时长")
        if any(
            set(record.task_ids) & set(preparation.task_ids) for record in scan.runtime.executions
        ):
            scan.fail("PREPARATION_HISTORY", "已开始或完成的工序不能改成提前准备")
        if any(
            dep.successor_id in scope and dep.predecessor_id not in scope
            for dep in recipe.dependencies
        ):
            scan.fail("PREPARATION_CLOSURE", "前置准备没有覆盖完整前置路径")
        if any(
            dep.predecessor_id in scope
            and dep.successor_id not in scope
            and dep.max_lag_sec is not None
            for dep in recipe.dependencies
        ):
            scan.fail("PREPARATION_CLOSURE", "有限保存时间不能由备料假设替代")
        if not any(
            operations[op].action in {"WAIT", "MARINATE", "CHILL", "FREEZE"} for op in scope
        ):
            scan.fail("PREPARATION_SOURCE", "声明没有明确准备操作")
        for op_id in scope:
            op = operations[op_id]
            if (
                op.action in {"HEAT", "PREHEAT", "UNLOAD", "FINISH", "UNKNOWN", "STIR"}
                or any(
                    use.resource_type == "DEVICE" and use.physical_resource_id != "fridge_1"
                    for use in op.resource_requirements
                )
                or op.execution_policy.fixed_batch_id
                or op.execution_policy.thermal_group_id
            ):
                scan.fail("PREPARATION_HEATING", "提前准备不能略过热加工或本轮厨具")
        if any(
            set(reservation.members) & set(preparation.task_ids)
            for reservation in problem.mandatory_programs.reservations
        ):
            scan.fail("PREPARATION_DEVICE", "提前准备不能移除本轮完整设备预约")
    if scan.runtime.details is None:
        scan.fail("PREPARATION_SOURCE", "提前准备缺少持久菜单声明")
        return
    details = scan.runtime.details
    for instance in problem.recipe_instances:
        declarations = [
            item
            for item in problem.advance_preparations
            if item.recipe_instance_id == instance.recipe_instance_id
        ]
        if not declarations:
            continue
        recipe = recipes[instance.recipe_id]
        prepared_ops = {op_id for item in declarations for op_id in item.operation_ids}
        supplies = {item.spec_id: item for item in recipe.ingredient_requirements}
        supplies.update(
            (item.spec_id, item) for op in recipe.operations for item in op.material_outputs
        )
        used: dict[str, Fraction] = defaultdict(Fraction)
        try:
            for operation in recipe.operations:
                if operation.operation_id not in prepared_ops:
                    continue
                for demand in operation.material_inputs:
                    used[demand.spec_id] += _share(demand, supplies[demand.spec_id])
                for loss in operation.losses:
                    used[loss.spec_id] += _share(
                        MaterialRequirement(
                            requirement_id="loss",
                            spec_id=loss.spec_id,
                            quantity_kind="EXACT",
                            quantity=loss.quantity,
                        ),
                        supplies[loss.spec_id],
                    )
        except (ValueError, KeyError) as exc:
            scan.fail("PREPARATION_MATERIAL", str(exc))
            continue
        produced_by = {
            output.spec_id: op.operation_id
            for op in recipe.operations
            for output in op.material_outputs
        }
        declaration_map = {op: item for item in declarations for op in item.operation_ids}
        for source_id, requirement in supplies.items():
            producer = produced_by.get(source_id)
            if producer is not None and producer not in prepared_ops:
                continue
            supply_id = stable_id("material", instance.recipe_instance_id.root, source_id)
            amount = 1 - used[source_id]
            quantity = requirement.exact_quantity
            expected = amount * (Fraction(quantity.value, quantity.scale) if quantity else 1)
            declaration = declaration_map.get(producer) if producer is not None else None
            matching = [
                lot
                for lot in details.lots
                if lot.spec_id == supply_id
                and (
                    lot.preparation_id is not None
                    if declaration
                    else "MENU_RECIPE_INPUT_DECLARATION" in lot.availability_evidence
                )
            ]
            if (
                expected < 0
                or sum((lot.produced.fraction() for lot in matching), Fraction(0)) != expected
            ):
                scan.fail("PREPARATION_MATERIAL", "声明供应与原配方边界份额不同", supply_id)
            for lot in matching:
                if declaration and (
                    lot.preparation_id != declaration.preparation_id
                    or lot.produced_by_execution_id is not None
                    or lot.source_event_id not in declaration.evidence_refs
                    or "USER_POLICY_ASSUMPTION" not in lot.availability_evidence
                    or scan.runtime.time_origin.offset(lot.produced_at)
                    != declaration.available_at_sec
                ):
                    scan.fail("PREPARATION_SOURCE", "备料供应来源不能冒充实测加工", lot.lot_id)
    if len(Counter(item.preparation_id for item in problem.advance_preparations)) != len(
        problem.advance_preparations
    ):
        scan.fail("PREPARATION_SOURCE", "重复提前备料身份")
