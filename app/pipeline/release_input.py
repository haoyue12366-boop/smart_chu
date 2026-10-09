"""将明确选择的开发/审核输入提交给独立知识门，再交给离线投影。"""

from app.domain.knowledge_release import ReviewedRelease
from app.domain.processing_rules import ProcessingRule
from app.pipeline.development import DevelopmentKnowledge
from app.validation.knowledge import validate_knowledge


def prepare_release_input(
    knowledge: DevelopmentKnowledge,
    *,
    release_id: str,
    recipe_ids: tuple[str, ...],
    knowledge_version: str | None = None,
    rules: tuple[ProcessingRule, ...] = (),
) -> ReviewedRelease:
    wanted = set(recipe_ids)
    if len(wanted) != len(recipe_ids) or not wanted:
        raise ValueError("发布菜谱集合为空或重复")
    recipes = tuple(r for r in knowledge.recipes if r.recipe_id.root in wanted)
    if len(recipes) != len(wanted):
        raise ValueError("发布包含未知菜谱")
    scope = knowledge.scope.model_copy(
        update={
            "required_recipes": tuple(
                r for r in knowledge.scope.required_recipes if r.recipe_id.root in wanted
            ),
            "recipe_contexts": tuple(
                c for c in knowledge.scope.recipe_contexts if c.recipe_id.root in wanted
            ),
        }
    )
    validation = validate_knowledge(recipes, knowledge.profiles, rules, scope)
    return ReviewedRelease(
        release_id=release_id,
        knowledge_version=knowledge_version or knowledge.knowledge_version,
        recipes=recipes,
        profiles=knowledge.profiles,
        rules=rules,
        scope=scope,
        provenance=knowledge.provenance,
        source_artifacts=knowledge.source_artifacts,
        validation=validation,
    )
