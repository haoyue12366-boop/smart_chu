"""Windows 技术验收绑定授权与完整开发知识，正式门槛保持独立。"""

import json

from app.config import AppSettings
from app.domain.base import content_hash
from app.domain.policy import SchedulingPolicy
from app.knowledge.loader import load_release, read_release_ref
from benchmarks.dataset import ROOT, suite_hash
from tests.p6_evidence_support import inputs, sha256


def test_p6_windows_inputs_bind_authorized_fixed_release_and_unchanged_formal_gate():
    expected = inputs()
    authorization = json.loads((ROOT / expected["authorization_path"]).read_bytes())
    assert authorization["source"] == "USER_MESSAGE"
    assert authorization["windows_deployment_quote"] == "完全使用 Windows，调整 P6-07 验收要求"
    assert authorization["additional_live_llm_calls_authorized"] is False
    settings = AppSettings()
    reference = read_release_ref(settings.release_root, settings.release_id)
    loaded = load_release(settings.release_root, reference)
    assert reference.release_kind == "development"
    for name in ("release_id", "snapshot_id", "manifest_hash"):
        assert getattr(reference, name) == expected[name]
    recipes = loaded.snapshot.knowledge.recipes
    assert len(recipes) == 100
    assert len({recipe.recipe_id.root for recipe in recipes}) == 100
    suite = json.loads((ROOT / "benchmarks/scenarios/full_suite.json").read_bytes())
    assert suite["suite_hash"] == suite_hash(suite) == expected["suite_hash"]
    policy = SchedulingPolicy.model_validate_json(settings.policy_path.read_bytes())
    assert content_hash(policy) == expected["policy_hash"]
    manifest = json.loads((ROOT / "scripts/verification_manifest.json").read_bytes())
    assert manifest["phases"]["P6"] == {
        "tasks": ["P6-08"],
        "required_phase_gates": ["P1:full", "P5"],
    }
    gate = manifest["phases"]["P6:development"]
    assert gate["tasks"] == [f"P6-{number:02}" for number in range(1, 8)]
    assert gate["deferred_formal_requirements"]
    auth_ref = gate["development_authorization_artifact"]
    assert auth_ref["path"] == expected["authorization_path"]
    assert auth_ref["sha256"] == sha256(ROOT / auth_ref["path"])
    assert manifest["tasks"]["P6-01"]["dependencies"] == ["P5-08"]
    assert gate["development_dependency_overrides"]["P5-01"] == ["P6-DEV-INPUTS"]
    for task in gate["tasks"]:
        assert manifest["tasks"][task]["tests"]
        assert manifest["tasks"][task]["artifacts"]
