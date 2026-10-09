"""真实样本子集经真实图投影的属性检验；不使用生成食谱冒充真实来源。"""

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.config import AppSettings
from app.domain.knowledge_release import ReviewedRelease
from app.knowledge.loader import load_release, read_release_ref
from app.pipeline.snapshot_export import SnapshotExporter
from app.validation.knowledge import validate_knowledge

pytestmark = pytest.mark.real_neo4j


@settings(
    max_examples=5, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(st.sets(st.integers(min_value=0, max_value=99), min_size=1, max_size=4))
def test_selected_real_recipes_keep_every_constraint_through_graph(graph, indices):
    _, projector, prefix = graph
    settings = AppSettings()
    reference = read_release_ref(settings.release_root, settings.release_id)
    knowledge = load_release(settings.release_root, reference).snapshot.knowledge
    chosen = tuple(knowledge.recipes[i].recipe_id.root for i in sorted(indices))
    wanted = set(chosen)
    recipes = tuple(r for r in knowledge.recipes if r.recipe_id.root in wanted)
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
    validation = validate_knowledge(recipes, knowledge.profiles, knowledge.rules, scope)
    source = ReviewedRelease.model_validate(
        {
            **knowledge.model_dump(),
            "release_id": prefix + "-" + "-".join(map(str, sorted(indices))),
            "recipes": recipes,
            "scope": scope,
            "validation": validation,
        }
    )
    projector.project(source)
    build = SnapshotExporter(projector).export_snapshot(source)
    restored = type(build.snapshot).model_validate_json(build.snapshot.model_dump_json())
    assert restored.knowledge == source
    assert restored.content_hash == build.snapshot.content_hash
    assert sum(len(r.dependencies) for r in source.recipes) == sum(
        len(row.dependencies) for row in build.index.recipes
    )
