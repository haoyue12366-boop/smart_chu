"""新规则发布保持原工艺和委托估计性质，旧准备包不可改写。"""

import json

import pytest

from app.domain.compatibility import GroupRuleSpec
from app.knowledge.loader import load_release, read_release_ref
from app.pipeline.p3_thermal_release import prepare_thermal_release, stage_thermal_sources
from tests.compiler_support import ROOT


@pytest.fixture(scope="module")
def prepared():
    return prepare_thermal_release(ROOT)


def test_new_version_preserves_all_recipes_and_source_evidence(prepared, tmp_path):
    source, audit = prepared
    assert source.validation.valid
    assert not source.validation.formal_release_eligible
    assert len(source.recipes) == 100
    assert sum(len(r.operations) for r in source.recipes) == 1562
    assert audit["process_changes"] == 0
    assert source.scope.rule_version == "development-shared-v2"
    assert len(source.rules) == 2
    thermal = next(r for r in source.rules if r.kind == "STRICT_TOGETHER")
    spec = GroupRuleSpec.model_validate_json(thermal.group_compatibility_predicate)
    assert spec.thermal_model == "COLD_LOAD_SETUP_PREHEAT_HEAT_STOP_UNLOAD"
    assert spec.duration_sec == 720
    assert thermal.review_status == "NEEDS_REVIEW"
    assert "p3-thermal-boundary-v1" in thermal.evidence_refs
    stage_thermal_sources(source, ROOT, tmp_path)
    for artifact in source.source_artifacts:
        artifact.verify(tmp_path)
    assert source.content_hash == prepare_thermal_release(ROOT)[0].content_hash


def test_original_release_and_active_pointer_are_unchanged(prepared):
    root = ROOT / "data/preparations/p3-v1/releases"
    old = load_release(root, read_release_ref(root, "development-v3-p3-preparation-v1-all"))
    rule = next(r for r in old.snapshot.knowledge.rules if r.kind == "STRICT_TOGETHER")
    assert (
        GroupRuleSpec.model_validate_json(rule.group_compatibility_predicate).thermal_model is None
    )
    active = json.loads((ROOT / "data/releases/active_release.json").read_text(encoding="utf-8"))
    assert active["release_id"] == "development-v3-rebased-v2-all"


@pytest.mark.parametrize(
    "change",
    [
        {"base_content_hash": "0" * 64},
        {"formal_human_review_complete": True},
        {"review_status": "APPROVED"},
        {"measured": True},
        {"outer_duration_sec": 1380},
    ],
)
def test_invalid_or_mislabeled_amendment_is_rejected(tmp_path, change):
    document = json.loads(
        (ROOT / "data/development/p3-thermal-boundary-v1.json").read_text(encoding="utf-8")
    )
    document.update(change)
    path = tmp_path / "amendment.json"
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError):
        prepare_thermal_release(ROOT, amendment_path=path)
