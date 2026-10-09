"""提取完整会话涉及的原菜谱，设备、规则及固定版本保持相同。"""

from app.domain.knowledge import MenuKnowledgeView
from app.domain.scheduling_problem import RecipeInstance


def session_knowledge(
    knowledge: MenuKnowledgeView, history: tuple[RecipeInstance, ...]
) -> MenuKnowledgeView:
    required = {instance.recipe_id for instance in history}
    recipes = tuple(recipe for recipe in knowledge.recipes if recipe.recipe_id in required)
    if {recipe.recipe_id for recipe in recipes} != required:
        raise ValueError("会话菜单存在固定知识版本中缺失的菜谱")
    return knowledge.model_copy(
        update={
            "recipes": recipes,
            "recipe_contexts": tuple(
                item for item in knowledge.recipe_contexts if item.recipe_id in required
            ),
        }
    )
