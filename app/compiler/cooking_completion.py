"""将发布工艺锚点绑定到本次菜谱实例，禁止猜测装盘时间。"""

from app.domain.cooking_completion import RecipeCookingCompletion
from app.domain.knowledge import MenuKnowledgeView
from app.domain.policy import SchedulingPolicy
from app.domain.scheduling_problem import LogicalTask, RecipeInstance


def bind_cooking_completions(
    knowledge: MenuKnowledgeView,
    menu: tuple[RecipeInstance, ...],
    tasks: tuple[LogicalTask, ...],
    policy: SchedulingPolicy,
) -> tuple[RecipeCookingCompletion, ...]:
    contexts = {c.recipe_id: c for c in knowledge.recipe_contexts}
    bindings = []
    for instance in menu:
        context = contexts.get(instance.recipe_id)
        rule = context.cooking_completion if context else None
        if rule is None:
            if policy.objective.spread_basis == "COOKING_FINISH":
                raise ValueError("出锅目标缺少审核边界：" + instance.name)
            continue
        by_operation = {
            t.operation_id: t for t in tasks if t.recipe_instance_id == instance.recipe_instance_id
        }
        if any(
            op not in by_operation or by_operation[op].operation.action == "FINISH"
            for op in rule.operation_ids
        ):
            raise ValueError("出锅锚点缺失或错误引用装盘收尾：" + instance.name)
        bindings.append(
            RecipeCookingCompletion(
                recipe_instance_id=instance.recipe_instance_id,
                task_ids=tuple(by_operation[op].task_id for op in rule.operation_ids),
                kind=rule.kind,
                evidence_refs=rule.evidence_refs,
            )
        )
    return tuple(bindings)
