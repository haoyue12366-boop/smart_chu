"""候选生成只携带当前菜单菜谱，静态规则、设备和知识身份保持完整。"""

from app.domain.knowledge import MenuKnowledgeView
from app.domain.scheduling_problem import RecipeInstance


def candidate_menu_knowledge(
    knowledge: MenuKnowledgeView, menu: tuple[RecipeInstance, ...]
) -> MenuKnowledgeView:
    recipe_ids = {instance.recipe_id for instance in menu}
    recipes = tuple(recipe for recipe in knowledge.recipes if recipe.recipe_id in recipe_ids)
    if len(recipes) != len(recipe_ids):
        raise ValueError("候选菜单的菜谱缺失或身份重复")
    if len(recipes) == len(knowledge.recipes):
        return knowledge
    # 仍严格校验当前完整菜谱；不重新构造菜单外工艺或改变输入的完整知识视图。
    return MenuKnowledgeView(
        release=knowledge.release,
        snapshot_schema_version=knowledge.snapshot_schema_version,
        snapshot_hash=knowledge.snapshot_hash,
        recipes=recipes,
        devices=knowledge.devices,
        profiles=knowledge.profiles,
        rules=knowledge.rules,
        provenance_index=knowledge.provenance_index,
        device_choices=knowledge.device_choices,
        recipe_contexts=tuple(
            context for context in knowledge.recipe_contexts if context.recipe_id in recipe_ids
        ),
    )
