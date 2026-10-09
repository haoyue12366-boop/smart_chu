"""独立核对可调度数据包，包含会破坏调度语义的回归案例。"""

import csv
import json
from pathlib import Path

from app.domain.canonical_recipe import CanonicalRecipeModel

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data/revisions/recipes_v3/scheduling_dataset.json"


def load():
    return json.loads(DATA.read_text(encoding="utf-8"))


def recipe(index):
    return load()["recipes"][index - 1]


def test_identity_and_complete_numeric_operations():
    dataset = load()
    with (ROOT / "docs/recipes_100.csv").open(encoding="utf-8-sig", newline="") as stream:
        source = list(csv.DictReader(stream))
    assert [(r["recipe_id"], r["name"]) for r in dataset["recipes"]] == [
        (r["菜谱id"], r["名称"]) for r in source
    ]
    for item in dataset["recipes"]:
        model = CanonicalRecipeModel.model_validate(item["canonical"])
        assert model.operations
        assert all(op.duration.execution_sec is not None for op in model.operations)
        assert not any(op.action in ("UNKNOWN", "MILESTONE") for op in model.operations)
        assert model.approval is None
        for op in model.operations:
            assert op.duration.execution_sec > 0
            assert op.provenance_refs
        ids = {op.operation_id.root for op in model.operations}
        assert set(item["operation_metadata"]) == ids


def test_no_notes_or_unsplit_resource_switches():
    for item in load()["recipes"]:
        for op in item["canonical"]["operations"]:
            assert op["action"] != "UNKNOWN"
            assert not op["description"].startswith(("审核备注", "说明"))
            meta = item["operation_metadata"][op["operation_id"]]
            assert meta["input_state"] and meta["output_state"]
        assert item["review_notes"] is not None


def test_explicit_parallel_paths_and_two_batches():
    asparagus = recipe(87)
    edges = asparagus["source_dependencies"]
    assert [4, 5] not in edges
    assert [4, 7] in edges and [6, 7] in edges
    scallops = recipe(98)
    assert [3, 4] not in scallops["source_dependencies"]
    shaomai = recipe(52)
    batches = [
        op
        for op in shaomai["canonical"]["operations"]
        if op["action"] == "HEAT" and op["execution_policy"]["fixed_batch_id"]
    ]
    assert len({op["execution_policy"]["fixed_batch_id"] for op in batches}) == 2
    assert all(op["duration"]["execution_sec"] == 600 for op in batches)


def test_interventions_and_resource_dictionary():
    data = load()
    resources = {r["resource_id"] for r in data["resources"]}
    for item in data["recipes"]:
        for use in item["resource_reservations"]:
            assert set(use["resource_options"]) <= resources
    event = recipe(75)["program_constraints"][0]
    assert event["remaining_sec"] == 900
    assert event["active_process_sec"] == 2700
    assert event["before_intervention_sec"] == 1800
    assert event["intervention_sec"] == 120
    assert event["timer_paused"] is True
    assert recipe(91)["program_constraints"][0]["intervention_sec"] == 240


def test_adaptations_keep_source_and_original_seconds():
    for index in (11, 41, 66):
        assert recipe(index)["adaptations"]
        assert recipe(index)["source_record"]
    for op in recipe(66)["canonical"]["operations"]:
        for resource in op["resource_requirements"]:
            values = {v["parameter"]: v["value"] for v in resource["configuration"]}
            assert values.get("temperature_c", 0) <= 230
    source_fifteen = [
        op
        for op in recipe(29)["canonical"]["operations"]
        if recipe(29)["operation_metadata"][op["operation_id"]]["source_step"] == 2
        and op["action"] == "HEAT"
    ]
    assert any(op["duration"]["execution_sec"] == 15 for op in source_fifteen)


def test_fixed_program_does_not_unload_at_screen_trigger():
    from scripts.check_scheduling_dataset import solve

    result = solve([recipe(91)])
    assert result["tasks"], result["status"]


def test_jelly_slicing_waits_for_setting_and_rice_is_independent():
    edges = recipe(89)["source_dependencies"]
    assert [9, 11] in edges
    assert [10, 11] not in edges
    assert [10, 12] in edges


def test_crab_rice_really_has_stove_frying():
    item = recipe(92)
    assert any(
        op["action"] == "HEAT"
        and item["operation_metadata"][op["operation_id"]]["source_step"] == 9
        and any(u["resource_id"] == "stove_choice" for u in op["resource_requirements"])
        for op in item["canonical"]["operations"]
    )


def test_white_roll_has_gelatin_and_butter_preparation():
    text = " ".join(op["description"] for op in recipe(60)["canonical"]["operations"])
    assert "泡发吉利丁" in text and "融化15克黄油" in text


def test_missing_preparations_are_explicit_and_precede_consumption():
    for row, extra in (
        (17, 1000),
        (22, 1000),
        (22, 1001),
        (32, 1000),
        (60, 1003),
        (77, 1000),
        (89, 1000),
    ):
        item = recipe(row)
        assert str(extra) in item["source_groups"], (row, extra)
