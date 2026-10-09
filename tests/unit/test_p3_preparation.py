"""准备包保留真实数据，仅增加可追溯的开发规则及技术版本。"""

import json

import pytest

from app.domain.compatibility import GroupRuleSpec
from app.knowledge.index import build_index
from app.pipeline.p3_preparation import prepare_p3_release
from tests.compiler_support import ROOT


@pytest.fixture(scope="module")
def prep_root(tmp_path_factory):
    from tests.preparation_support import stage_preparation_root

    return stage_preparation_root(tmp_path_factory.mktemp("p3-source-replay"))


def test_preparation_preserves_100_recipe_processes_and_binds_two_rules(prep_root):
    source, audit = prepare_p3_release(prep_root)
    assert len(source.recipes) == 100
    assert sum(len(r.operations) for r in source.recipes) == 1562
    assert len(source.rules) == 2
    assert source.scope.release_kind == "development"
    assert source.validation.valid
    assert not source.validation.formal_release_eligible
    assert audit["process_changes"] == 0
    assert audit["review_decision_count"] == 13
    recipes = {r.recipe_id: r for r in source.recipes}
    for rule in source.rules:
        assert rule.rule_version == source.scope.rule_version
        spec = GroupRuleSpec.model_validate_json(rule.group_compatibility_predicate)
        assert spec.authority == "DELEGATED_DEVELOPMENT_ESTIMATE"
        for b in spec.bindings:
            assert b.recipe_hash == recipes[b.recipe_id].semantic_hash()
    for artifact in source.source_artifacts:
        artifact.verify(prep_root)


def test_preparation_index_can_locate_rules_without_editing_source_operations(prep_root):
    source, _ = prepare_p3_release(prep_root)
    index = build_index(source)
    reverse = {b.key: b.members for b in index.rule_reverse_index}
    assert set(reverse) == {r.rule_id for r in source.rules}
    assert all(len(members) == 2 for members in reverse.values())


def test_review_of_another_snapshot_is_rejected(tmp_path, prep_root):
    path = ROOT / "data/development/p3-delegated-review-v1.json"
    review = json.loads(path.read_text(encoding="utf-8"))
    review["source_snapshot_hash"] = "0" * 64
    copy = tmp_path / "review.json"
    copy.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="快照"):
        prepare_p3_release(prep_root, review_path=copy)


def test_estimated_rule_cannot_be_promoted_to_formal_approval(tmp_path, prep_root):
    path = ROOT / "data/development/p3-delegated-review-v1.json"
    review = json.loads(path.read_text(encoding="utf-8"))
    review["processing_rules"][0]["review_status"] = "APPROVED"
    copy = tmp_path / "review.json"
    copy.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="委托"):
        prepare_p3_release(prep_root, review_path=copy)


def test_deferred_items_never_become_preparation_rules(prep_root):
    source, audit = prepare_p3_release(prep_root)
    assert {r.rule_id for r in source.rules} == {"P3-S02-delegated-v1", "P3-H02-delegated-v1"}
    assert audit["shared_runtime_enabled"] is False
    assert len(audit["recipe_bindings"]) == 100


def test_prep_does_not_change_current_active_release(prep_root):
    path = ROOT / "data/releases/active_release.json"
    previous = path.read_bytes()
    prepare_p3_release(prep_root)
    assert path.read_bytes() == previous
