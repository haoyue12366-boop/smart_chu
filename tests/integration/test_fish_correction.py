"""用户授权修正的真实菜谱；源版本不可覆盖，授权不等于全菜人工批准。"""

import hashlib
import json
from pathlib import Path

from app.pipeline.development import load_development_knowledge
from app.validation.knowledge import validate_knowledge
from scripts.correct_fish_development import BASE, FISH_ID, VERSION, build_corrected_version

ROOT = Path(__file__).resolve().parents[2]


def test_correction_preserves_source_and_all_other_recipes(tmp_path):
    before = (ROOT / BASE).read_bytes()
    result = build_corrected_version(ROOT, tmp_path)
    after = json.loads((tmp_path / "dataset.json").read_text("utf-8"))
    original = json.loads(before)
    assert (ROOT / BASE).read_bytes() == before
    assert result["base_sha256"] == hashlib.sha256(before).hexdigest()
    assert after["knowledge_version"] == VERSION
    assert after["approved_count"] == 0
    for old, new in zip(original["recipes"], after["recipes"], strict=True):
        if old["recipe_id"] != FISH_ID:
            assert old == new
    row = next(r for r in after["recipes"] if r["recipe_id"] == FISH_ID)
    ops = {o["operation_id"]: o for o in row["canonical"]["operations"]}
    assert "op_002_01" not in ops and "op_8002_01" not in ops
    wash = ops["op_002_02"]
    assert wash["action"] == "WASH"
    assert wash["duration"]["execution_sec"] == 15
    assert wash["duration"]["lower_sec"] == wash["duration"]["upper_sec"] == 15
    assert [u["resource_id"] for u in wash["resource_requirements"]] == ["human_1"]
    assert wash["material_outputs"][0]["spec_id"] == "scalded_fish"
    assert ops["op_1001_01"]["duration"]["execution_sec"] == 300
    old_ops = {
        o["operation_id"]: o
        for o in next(r for r in original["recipes"] if r["recipe_id"] == FISH_ID)["canonical"][
            "operations"
        ]
    }
    for oid in ("op_007_01", "op_008_01", "op_008_02", "op_009_01"):
        assert ops[oid] == old_ops[oid]
    pairs = {(d["predecessor_id"], d["successor_id"]) for d in row["canonical"]["dependencies"]}
    assert ("op_8001_01", "op_002_02") in pairs
    assert ("op_002_02", "op_003_01") in pairs
    assert row["canonical"]["approval"] is None
    assert build_corrected_version(ROOT, tmp_path) == result


def test_corrected_real_development_has_100_valid_seconds_paths():
    knowledge = load_development_knowledge(ROOT, f"data/development/{VERSION}/dataset.json")
    report = validate_knowledge(knowledge.recipes, knowledge.profiles, (), knowledge.scope)
    assert report.valid, report.violations
    assert len(report.usable_recipe_ids) == 100
    assert not report.formal_release_eligible
    assert report.single_recipe_solve_status == "NOT_RUN"
