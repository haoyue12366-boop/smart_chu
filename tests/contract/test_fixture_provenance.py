import csv
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from app.domain.canonical_recipe import CanonicalRecipeModel
from app.domain.resources import ResourceUse

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "tests/fixtures/fixture_manifest.json"


def test_v3_import_preserves_ai_values_without_creating_approval(tmp_path, monkeypatch):
    from scripts import update_p0_samples_from_revision as importer

    paths = {MANIFEST, ROOT / "docs/recipes_100.csv"}
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    paths.update(ROOT / entry["path"] for entry in manifest["real_samples"])
    for name in ("MANIFEST", "REVISION", "CSV", "MAP"):
        original = getattr(importer, name)
        paths.add(original)
        monkeypatch.setattr(importer, name, tmp_path / original.relative_to(ROOT))
    revision = json.loads((ROOT / importer.REVISION.relative_to(tmp_path)).read_text("utf-8"))
    paths.update(ROOT / name for name in revision["source_hashes"])
    # 新开发版本只依赖已取得的材料；缺失历史由 provenance_gaps 明确验收。
    paths.update(ROOT / name for name in manifest["source_hashes"] if (ROOT / name).is_file())
    for path in paths:
        target = tmp_path / path.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        origin = path
        if not origin.is_file():
            # A workspace document was removed after publication. Reproduce from
            # its archived bytes, bound to the original dataset digest, only here.
            relative = path.relative_to(ROOT).as_posix()
            expected = revision["source_hashes"][relative]
            origin = (
                ROOT
                / "data/preparations/p3-thermal-v1/releases"
                / "development-v3-p3-thermal-v1-all"
                / relative
            )
            assert hashlib.sha256(origin.read_bytes()).hexdigest() == expected
        shutil.copyfile(origin, target)
    (tmp_path / "data/issues").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(importer, "ROOT", tmp_path)

    importer.main()

    converted_manifest = json.loads(importer.MANIFEST.read_text(encoding="utf-8"))
    assert converted_manifest["knowledge_version"] == importer.VERSION
    assert converted_manifest["provenance_gaps"]
    assert converted_manifest["review_gate"]["approved_count"] == 0
    for entry in converted_manifest["real_samples"]:
        sample = json.loads((tmp_path / entry["path"]).read_text(encoding="utf-8"))
        draft = CanonicalRecipeModel.model_validate(sample["canonical_draft"])
        assert draft.review_status == sample["review_status"] == "NEEDS_REVIEW"
        assert draft.approval is None and sample["review_records"] == []
        assert all(op.duration.execution_sec is not None for op in draft.operations)
        original = next(r for r in revision["recipes"] if r["recipe_id"] == entry["recipe_id"])
        expected = CanonicalRecipeModel.model_validate(original["canonical"])
        assert draft.operations == expected.operations
        assert draft.dependencies == expected.dependencies
        assert draft.material_specs == expected.material_specs
        assert sample["unresolved_issue_ids"]
        assert any(e["origin"] == "SOURCE_EXPLICIT" for e in sample["evidence"])
        assert any(e["origin"] == "MODEL_SUGGESTION" for e in sample["evidence"])
        assert (
            sample["refinement_detail"]["sha256"]
            == hashlib.sha256(importer.REVISION.read_bytes()).hexdigest()
        )


def test_legacy_generator_cannot_overwrite_v3_samples(tmp_path, monkeypatch):
    from scripts import prepare_p0_samples as legacy

    source = tmp_path / "docs/recipes_100.csv"
    source.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "docs/recipes_100.csv", source)
    path = tmp_path / "tests/fixtures/reviewed_sample/5d54bae2a9114174727c8b20.json"
    path.parent.mkdir(parents=True)
    original = json.dumps({"knowledge_version": "development-scheduling-v3"})
    path.write_text(original, encoding="utf-8")
    monkeypatch.setattr(legacy, "ROOT", tmp_path)

    with pytest.raises(ValueError, match="拒绝覆盖"):
        legacy.main()

    assert path.read_text(encoding="utf-8") == original


def test_manifest_sources_and_twelve_distinct_ids():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    rows = {
        r["菜谱id"]: r
        for r in csv.DictReader(
            (ROOT / "docs/recipes_100.csv").read_text(encoding="utf-8-sig").splitlines()
        )
    }
    real = manifest["real_samples"]
    assert len(real) >= 12
    assert len({entry["recipe_id"] for entry in real}) == len(real)
    for entry in real:
        path = ROOT / entry["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]
        sample = json.loads(path.read_text(encoding="utf-8"))
        assert sample["source_kind"] == "REAL_SOURCE"
        assert sample["source_record"] == rows[entry["recipe_id"]]
        assert sample["review_status"] in ("NEEDS_REVIEW", "APPROVED")
        draft = CanonicalRecipeModel.model_validate(sample["canonical_draft"])
        assert draft.recipe_id.root == entry["recipe_id"]
        assert draft.review_status == sample["review_status"]
        assert (
            sample["source_hash"]
            == hashlib.sha256((ROOT / sample["source_file"]).read_bytes()).hexdigest()
        )


def test_coverage_and_synthetic_cases():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert set(manifest["coverage"]) >= {
        "long_preparation",
        "recipe_parallelism",
        "two_burner_contention",
        "device_relay",
        "different_heat_durations",
        "explicit_batches",
        "intervention",
        "duplicate_names",
        "shared_cutting_candidate",
    }
    for entry in manifest["synthetic_samples"]:
        assert entry["source_kind"] == "SYNTHETIC"
        assert entry["expected_reason"]
        if entry["expected_result"] == "拒绝":
            invalid = json.loads((ROOT / entry["path"]).read_text(encoding="utf-8"))
            with pytest.raises(ValueError):
                ResourceUse.model_validate(invalid)
        assert hashlib.sha256((ROOT / entry["path"]).read_bytes()).hexdigest() == entry["sha256"]
    good = json.loads((ROOT / manifest["synthetic_samples"][0]["path"]).read_text(encoding="utf-8"))
    assert CanonicalRecipeModel.model_validate(good)


@pytest.mark.review_gate
def test_reviewed_samples_ready_for_p1_core():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    missing = []
    for entry in manifest["real_samples"]:
        sample = json.loads((ROOT / entry["path"]).read_text(encoding="utf-8"))
        if sample["review_status"] != "APPROVED":
            missing.append(f"{entry['recipe_id']} {sample['name']}")
        else:
            approved = CanonicalRecipeModel.model_validate(sample["canonical_draft"])
            assert approved.approval is not None
            assert sample["review_records"], "真实审核记录不可为空"
            assert sample["unresolved_issue_ids"] == []
    assert not missing, "缺少真实人工审核依据：" + "、".join(missing)


def test_refinement_does_not_reheat_prepared_ingredients():
    from scripts.refine_recipe_steps import normalize

    step = {
        "kind": "人工",
        "text": "将炒香的配料盛于小碗中。",
        "resource": "锅具+容器",
        "time": "",
        "note": "",
        "source_steps": [4],
        "source_annotations": [],
        "source_bodies": [],
        "prior_human_suggestion": None,
        "prior_duration_suggestion": None,
        "patched": True,
        "changes": [],
    }
    converted = normalize(step, "example", {"oven_recipe_ids": []})
    assert converted["kind"] == "人工"
    assert "灶具" not in converted["resource"]


def test_refinement_notes_are_not_process_duration():
    from scripts.refine_recipe_steps import normalize

    step = {
        "kind": "说明",
        "text": "原35分钟建议值不能代替预热和各运行段。",
        "resource": "",
        "time": "",
        "note": "",
        "source_steps": [],
        "source_annotations": [],
        "source_bodies": [],
        "prior_human_suggestion": None,
        "prior_duration_suggestion": None,
        "patched": True,
        "changes": [],
    }
    converted = normalize(step, "example", {"oven_recipe_ids": []})
    assert converted["source_time_constraint"]["relation"] == "unknown"


def test_refined_samples_bind_current_v3_and_unapproved_ai_time():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for entry in manifest["real_samples"]:
        sample = json.loads((ROOT / entry["path"]).read_text(encoding="utf-8"))
        ref = sample["refinement_source"]
        assert ref["path"] == "data/development/development-v3-rebased-v1/dataset.json"
        assert hashlib.sha256((ROOT / ref["path"]).read_bytes()).hexdigest() == ref["sha256"]
        draft = CanonicalRecipeModel.model_validate(sample["canonical_draft"])
        assert draft.ingredient_requirements
        assert draft.dependencies
        assert all(op.duration.execution_sec is not None for op in draft.operations)
        assert all(op.review_status == "NEEDS_REVIEW" for op in draft.operations)
        assert draft.approval is None
        evidence = {e["provenance_id"]: e for e in sample["evidence"]}
        assert all(op.duration.source_ref in evidence for op in draft.operations)
        assert any(e["origin"] == "MODEL_SUGGESTION" for e in evidence.values())
        assert not any(op.operation_id.root.startswith("raw_step") for op in draft.operations)

    fish = _sample("5dad79866601a865649f9cb7")
    steam = next(
        op for op in fish.operations if op.action == "HEAT" and "蒸8分钟" in op.description
    )
    assert steam.duration.nominal_sec == 480
    assert steam.duration.fixed_process_time
    chicken = _sample("5c8646fe98d5bc7e184a90bc")
    frozen = next(op for op in chicken.operations if op.action == "FREEZE")
    assert frozen.duration.lower_sec == 86400
    assert frozen.duration.upper_sec is None


def _sample(recipe_id):
    path = ROOT / f"tests/fixtures/reviewed_sample/{recipe_id}.json"
    return CanonicalRecipeModel.model_validate(
        json.loads(path.read_text(encoding="utf-8"))["canonical_draft"]
    )


def test_real_parallel_and_two_batches_survive_conversion():
    asparagus_record = json.loads(
        (ROOT / "tests/fixtures/reviewed_sample/5dad486b6601a865649f9c15.json").read_text("utf-8")
    )
    asparagus = CanonicalRecipeModel.model_validate(asparagus_record["canonical_draft"])
    edges = {(d.predecessor_id.root, d.successor_id.root) for d in asparagus.dependencies}
    groups = asparagus_record["scheduling_context"]["source_groups"]
    assert (groups["4"][-1], groups["5"][0]) not in edges
    assert {
        (groups["4"][-1], groups["7"][0]),
        (groups["6"][-1], groups["7"][0]),
    } <= edges
    shaomai = _sample("5cc7e0404a21a4301960aef1")
    heats = [
        op
        for op in shaomai.operations
        if op.action == "HEAT" and op.execution_policy.batch_policy == "FIXED_RECIPE"
    ]
    assert len(heats) == 2
    assert len({op.execution_policy.fixed_batch_id for op in heats}) == 2
    assert all(op.duration.execution_sec > 0 for op in heats)
    assert all(op.review_status == "NEEDS_REVIEW" for op in heats)


def test_remainder_event_keeps_source_trigger_and_marks_ai_program_duration():
    recipe_id = "66cec69b1142545e3856e07e"
    sample = json.loads(
        (ROOT / f"tests/fixtures/reviewed_sample/{recipe_id}.json").read_text(encoding="utf-8")
    )
    draft = CanonicalRecipeModel.model_validate(sample["canonical_draft"])
    program = sample["process_constraints"][0]
    assert program["trigger_type"] == "remaining_time"
    assert program["trigger_value"] == program["remaining_sec"] == 900
    assert program["origin"] == "MODEL_SUGGESTION"
    assert program["active_process_sec"] == program["before_intervention_sec"] + 900
    operations = {op.operation_id.root: op for op in draft.operations}
    after = [operations[key] for key in program["after_group"]]
    assert sum(op.duration.execution_sec for op in after if op.action == "HEAT") == 900
    assert all(op.review_status == "NEEDS_REVIEW" for op in after)
    assert any("HUMAN_REVIEW" in issue for issue in sample["unresolved_issue_ids"])


def test_duplicate_recipe_names_keep_separate_quantities():
    first = _sample("58e70b129f6429675ec80601")
    second = _sample("5fe197255f8f38795ea6fe79")
    assert first.name == second.name and first.recipe_id != second.recipe_id
    assert first.ingredient_requirements[0].quantity.as_decimal() == 350
    assert second.ingredient_requirements[0].quantity_kind == "RANGE"
    assert second.ingredient_requirements[0].quantity.as_decimal() == 300
    assert second.ingredient_requirements[0].upper_quantity.as_decimal() == 400
    assert second.ingredient_requirements[1].quantity.unit == "茶匙"
    assert second.ingredient_requirements[1].quantity.as_decimal() == 0.25
