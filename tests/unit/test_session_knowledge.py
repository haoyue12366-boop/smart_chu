"""会话知识提取保留固定版本、原工艺和取消后的历史来源。"""

import pytest

from app.domain.scheduling_problem import RecipeInstance
from app.services.session_knowledge import session_knowledge
from tests.runtime_support import p4_knowledge


def selection(recipe, identity):
    return RecipeInstance(recipe_instance_id=identity, recipe_id=recipe.recipe_id, name=recipe.name)


def test_menu_scope_preserves_fact_objects_and_all_device_rules():
    original = p4_knowledge()
    recipes = (original.recipes[0], original.recipes[1])
    view = session_knowledge(
        original, tuple(selection(recipe, str(i)) for i, recipe in enumerate(recipes))
    )
    assert view.recipes == recipes
    assert all(left is right for left, right in zip(view.recipes, recipes, strict=True))
    assert view.recipe_contexts == tuple(
        item
        for item in original.recipe_contexts
        if item.recipe_id in {r.recipe_id for r in recipes}
    )
    for field in ("release", "snapshot_hash", "devices", "profiles", "rules", "provenance_index"):
        assert getattr(view, field) is getattr(original, field)
    assert len(original.recipes) == 100 and len(view.recipes) == 2


def test_duplicate_instances_and_cancelled_history_keep_one_original_recipe():
    original = p4_knowledge()
    current, cancelled = original.recipes[:2]
    # 调用方传入完整会话菜单，包括已经取消但有执行历史的实例。
    history = (selection(current, "a"), selection(current, "b"), selection(cancelled, "retired"))
    assert session_knowledge(original, history).recipes == (current, cancelled)


def test_unknown_recipe_cannot_be_silently_removed_from_session_scope():
    original = p4_knowledge()
    unknown = RecipeInstance(recipe_instance_id="unknown", recipe_id="unknown", name="未知菜")
    with pytest.raises(ValueError, match="固定知识"):
        session_knowledge(original, (unknown,))
