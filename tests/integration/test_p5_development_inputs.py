"""P5 开发授权与当前实施输入门槛；不将历史准备或外部未知条件当作验收。"""

import hashlib
import json
from pathlib import Path

from app.api.competition_profiles import CompetitionProfile
from tests.runtime_support import p4_knowledge

ROOT = Path(__file__).resolve().parents[2]


def test_p5_development_gate_registers_all_eight_tasks_and_real_frontend_checks():
    manifest = json.loads((ROOT / "scripts/verification_manifest.json").read_bytes())
    gate = manifest["phases"]["P5:development"]
    assert gate["tasks"] == [f"P5-{number:02}" for number in range(1, 9)]
    assert gate["required_phase_gates"] == ["P4:development"]
    assert manifest["phases"]["P5"]["required_phase_gates"] == ["P1:core"]
    assert gate["deferred_formal_requirements"]
    reference = gate["development_authorization_artifact"]
    raw = (ROOT / reference["path"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == reference["sha256"]
    assert json.loads(raw)["source"] == "USER_MESSAGE"
    for task in gate["tasks"]:
        specification = manifest["tasks"][task]
        assert specification["tests"] and specification["artifacts"]
    assert "tests/integration/test_web_quality.py" in manifest["tasks"]["P5-06"]["tests"]
    assert "tests/integration/test_web_browser.py" in manifest["tasks"]["P5-07"]["tests"]
    assert "tests/integration/test_web_competition.py" in manifest["tasks"]["P5-08"]["tests"]


def test_version_two_inputs_bind_fixed_knowledge_authorized_profile_and_original_materials():
    inputs = json.loads((ROOT / "data/preparations/p5-v2/implementation_inputs.json").read_bytes())
    assert inputs["kind"] == "IMPLEMENTATION_INPUTS"
    assert inputs["functional_acceptance"] == "REQUIRES_ACTUAL_P5_GATE"
    assert inputs["profile"] == CompetitionProfile().model_dump(mode="json")
    for name, expected in inputs["input_hashes"].items():
        path = (ROOT / name).resolve()
        assert path.is_relative_to(ROOT)
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected
    knowledge = p4_knowledge()
    assert knowledge.release.model_dump(mode="json") == inputs["release"]
    assert len(knowledge.recipes) == inputs["recipe_count"] == 100
    assert sum(len(r.operations) for r in knowledge.recipes) == inputs["operation_count"] == 1562
    assert len(knowledge.profiles) == inputs["profile_count"] == 27
    assert inputs["historical_preparation"]["role"] == "ARCHIVED_PRE_IMPLEMENTATION"
    assert inputs["historical_preparation"]["path"] == "data/preparations/p5-v1/manifest.json"
