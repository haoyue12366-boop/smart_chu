"""独立从已发布上下文核对出锅边界，不信任编译器自报锚点。"""

from app.domain.cooking_completion import RecipeCookingCompletion
from app.validation.schedule_context import Scan


def check_cooking_completion_sources(scan: Scan) -> None:
    supplied_tasks = {t for s in scan.problem.fixed_supply_fulfillments for t in s.task_ids}
    supplied_tasks.update(t for c in scan.problem.inventory_supply_candidates for t in c.covers)
    supplied_tasks.update(t for item in scan.problem.advance_preparations for t in item.task_ids)
    if any(t in supplied_tasks for b in scan.problem.cooking_completions for t in b.task_ids):
        scan.fail("COOKING_COMPLETION_SOURCE", "库存接收时刻不能替代原出锅时刻")
    contexts = {c.recipe_id: c for c in scan.knowledge.recipe_contexts}
    expected = []
    for instance in scan.problem.recipe_instances:
        context = contexts.get(instance.recipe_id)
        rule = context.cooking_completion if context else None
        if rule is None:
            if scan.problem.policy.objective.spread_basis == "COOKING_FINISH":
                scan.fail(
                    "COOKING_COMPLETION_SOURCE",
                    "发布知识缺少出锅边界",
                    instance.recipe_instance_id.root,
                )
            continue
        operations = {
            t.operation_id: t
            for t in scan.problem.logical_tasks
            if t.recipe_instance_id == instance.recipe_instance_id
        }
        if any(
            op not in operations or operations[op].operation.action == "FINISH"
            for op in rule.operation_ids
        ):
            scan.fail(
                "COOKING_COMPLETION_SOURCE",
                "发布边界工序缺失或误用装盘收尾",
                instance.recipe_instance_id.root,
            )
            continue
        expected.append(
            RecipeCookingCompletion(
                recipe_instance_id=instance.recipe_instance_id,
                task_ids=tuple(operations[op].task_id for op in rule.operation_ids),
                kind=rule.kind,
                evidence_refs=rule.evidence_refs,
            )
        )
    if tuple(expected) != scan.problem.cooking_completions:
        scan.fail("COOKING_COMPLETION_SOURCE", "编译出锅锚点与发布工艺不一致")
