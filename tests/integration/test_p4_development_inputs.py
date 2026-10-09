"""实际授权、固定 P4 发布身份及 100 份历史计划；不代替运行功能验收。"""

import hashlib
import json
from pathlib import Path

from scripts.check_p4_saved_plans import validate
from tests.runtime_support import p4_knowledge

ROOT = Path(__file__).resolve().parents[2]


def test_development_gate_binds_actual_authorization_and_all_ten_tasks():
    manifest = json.loads((ROOT / "scripts/verification_manifest.json").read_bytes())
    entry = manifest["phases"]["P4:development"]
    reference = entry["development_authorization_artifact"]
    raw = (ROOT / reference["path"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == reference["sha256"]
    authority = json.loads(raw)
    assert authority["source"] == "USER_MESSAGE"
    assert authority["actor_kind"] == "DELEGATED_AGENT" and authority["approve_for_p4"]
    assert entry["deferred_formal_requirements"]
    assert manifest["phases"]["P4"]["required_phase_gates"] == ["P1:core"]
    assert "P3-07" in manifest["tasks"]["P4-DEV-INPUTS"]["dependencies"]
    assert "P3-DEV-INPUTS" in entry["development_dependency_overrides"]["P3-01"]
    for number in range(1, 11):
        task = manifest["tasks"][f"P4-{number:02}"]
        assert task["tests"] and task["artifacts"]
    knowledge = p4_knowledge()
    assert knowledge.release.release_id == "delegated-v3-p4-preparation-v1-all"
    assert len(knowledge.recipes) == 100
    assert sum(len(recipe.operations) for recipe in knowledge.recipes) == 1562
    assert len(knowledge.profiles) == 27
    assert all(recipe.review_status == "APPROVED" for recipe in knowledge.recipes)
    assert not any(rule.kind in {"INVENTORY", "RECOVERY"} for rule in knowledge.rules)


def test_all_one_hundred_preparation_plans_still_validate_independently(tmp_path):
    report = validate(tmp_path / "p4-historical-plans.json")
    assert report["status"] == "PASSED"
    assert report["persisted_plan_revalidation_count"] == 100
    assert report["operation_count"] == 1562
