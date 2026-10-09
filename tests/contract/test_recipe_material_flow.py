"""物料装配契约；合成反例与真实披萨程序分别验证。"""

import json
from copy import deepcopy
from fractions import Fraction
from pathlib import Path

import pytest

from app.domain.canonical_recipe import CanonicalRecipeModel
from scripts.attach_recipe_materials import attach_materials

ROOT = Path(__file__).resolve().parents[2]


def fixture_recipe(actions, count=2):
    specs = [
        {
            "spec_id": f"ingredient_{i:03}",
            "ingredient_id": f"test:ingredient_{i:03}",
            "name": f"食材{i}",
            "state": "生料",
        }
        for i in range(1, count + 1)
    ]
    operations = [
        {
            "operation_id": name,
            "action": action,
            "description": f"食材处理{name}",
            "duration": {"execution_sec": 60},
        }
        for name, action in actions
    ]
    operations.append(
        {
            "operation_id": "finish",
            "action": "FINISH",
            "description": "装盘上桌",
            "duration": {"execution_sec": 60},
        }
    )
    model = CanonicalRecipeModel.model_validate(
        {
            "schema_version": "1.0",
            "recipe_id": "synthetic-material-contract",
            "name": "合成契约样例",
            "recipe_version": "test",
            "ingredient_requirements": [
                {
                    "requirement_id": f"demand_{i:03}",
                    "spec_id": spec["spec_id"],
                    "quantity_kind": "EXACT",
                    "quantity": {"value": 7, "unit": "g", "scale": 3},
                }
                for i, spec in enumerate(specs, 1)
            ],
            "material_specs": specs,
            "operations": operations,
            "dependencies": [],
            "provenance_refs": ["synthetic:test"],
        }
    )
    return {
        "recipe_id": "synthetic-material-contract",
        "name": "合成契约样例",
        "canonical": model.model_dump(mode="json"),
        "source_groups": {1: [name for name, _ in actions]},
        "operation_metadata": {},
    }


def output(key, sources=None):
    result = {"id": key, "name": f"食物{key}", "state": "加工后真实食物状态"}
    if sources is not None:
        result["from_inputs"] = sources
    return result


def entry(stages, finals):
    return {"row": 1, "stages": stages, "final_outputs": finals, "notes": []}


def op(result, key):
    return next(x for x in result["canonical"]["operations"] if x["operation_id"] == key)


def test_fractional_raw_quantity_exact_and_retained_remainder():
    result = fixture_recipe([("cut", "CUT")], count=1)
    mapping = entry(
        [
            {
                "step": 1,
                "inputs": [{"id": "raw_001", "fraction": "1/2"}],
                "outputs": [output("cut_food")],
            }
        ],
        ["cut_food"],
    )
    attach_materials(result, mapping)
    used = op(result, "cut")["material_inputs"][0]["quantity"]
    assert Fraction(used["value"], used["scale"]) == Fraction(7, 6)
    assert used["unit"] == "g"
    assert result["material_flow"]["remaining"]["raw_001"] == "1/2"
    assert op(result, "finish")["material_inputs"][0]["spec_id"] == "cut_food"
    assert op(result, "finish")["material_outputs"][0]["spec_id"] == "finaldish"
    CanonicalRecipeModel.model_validate(result["canonical"])


def test_preheat_has_no_food_and_multiple_outputs_keep_distinct_lineage():
    result = fixture_recipe([("preheat", "PREHEAT"), ("load", "LOAD"), ("heat", "HEAT")])
    result["source_groups"] = {"1": ["preheat", "load", "heat"]}
    mapping = entry(
        [
            {
                "step": 1,
                "inputs": ["raw_001", "raw_002"],
                "outputs": [output("a", ["raw_001"]), output("b", ["raw_002"])],
            }
        ],
        ["a", "b"],
    )
    attach_materials(result, mapping)
    assert not op(result, "preheat")["material_inputs"]
    assert not op(result, "preheat")["material_outputs"]
    assert op(result, "load")["material_inputs"]
    assert op(result, "heat")["material_inputs"]
    specs = {s["spec_id"]: s for s in result["canonical"]["material_specs"]}
    assert specs["a"]["composition"] == ["ingredient_001"]
    assert specs["b"]["composition"] == ["ingredient_002"]
    assert all(x["quantity_kind"] == "QUALITATIVE" for x in op(result, "heat")["material_outputs"])
    edges = {(x["predecessor_id"], x["successor_id"]) for x in result["canonical"]["dependencies"]}
    assert ("load", "heat") in edges and ("heat", "finish") in edges


@pytest.mark.parametrize("failure", ["unknown", "overuse", "cycle"])
def test_invalid_material_flow_rejected_without_mutating_recipe(failure):
    result = fixture_recipe([("cut", "CUT"), ("cook", "HEAT")], count=1)
    result["source_groups"] = {1: ["cut"], 2: ["cook"]}
    stages = [
        {"step": 1, "inputs": ["raw_001"], "outputs": [output("a")]},
        {"step": 2, "inputs": ["a"], "outputs": [output("b")]},
    ]
    if failure == "unknown":
        stages[0]["inputs"] = ["absent"]
    elif failure == "overuse":
        stages[1]["inputs"].append("raw_001")
    else:
        result["canonical"]["dependencies"] = [
            {
                "predecessor_id": "cook",
                "successor_id": "cut",
                "reason": "合成反向依赖",
                "evidence_refs": ["synthetic:test"],
            }
        ]
    before = deepcopy(result)
    with pytest.raises(ValueError):
        attach_materials(result, entry(stages, ["b"]))
    assert result == before


def test_two_stages_on_one_atomic_operation_do_not_create_self_dependency():
    result = fixture_recipe([("prepare", "PREPARE")])
    mapping = entry(
        [
            {"step": 1, "inputs": ["raw_001"], "outputs": [output("a")]},
            {"step": 1, "inputs": ["a", "raw_002"], "outputs": [output("b")]},
        ],
        ["b"],
    )
    attach_materials(result, mapping)
    assert {x["spec_id"] for x in op(result, "prepare")["material_inputs"]} == {
        "ingredient_001",
        "ingredient_002",
    }
    assert [x["spec_id"] for x in op(result, "prepare")["material_outputs"]] == ["b"]
    assert all(
        x["predecessor_id"] != x["successor_id"] for x in result["canonical"]["dependencies"]
    )


def test_real_pizza_toppings_enter_after_blind_bake():
    dataset = json.loads(
        (ROOT / "data/revisions/recipes_v3/scheduling_dataset.json").read_text(encoding="utf-8")
    )
    maps = json.loads(
        (ROOT / "data/revisions/recipes_v3/material_map_76_100.json").read_text(encoding="utf-8")
    )
    result = deepcopy(dataset["recipes"][75])
    attach_materials(result, next(x for x in maps if x["row"] == 76))
    first_heat = op(result, "op_005_03")
    specs = {s["spec_id"]: s for s in result["canonical"]["material_specs"]}
    for requirement in first_heat["material_inputs"]:
        assert not {"ingredient_007", "ingredient_008"}.intersection(
            specs[requirement["spec_id"]]["composition"]
        )
    added = {x["spec_id"] for x in op(result, "op_005_05")["material_inputs"]}
    assert {"ingredient_007", "ingredient_008"} <= added
    CanonicalRecipeModel.model_validate(result["canonical"])


def test_device_configuration_and_empty_release_have_no_food():
    result = fixture_recipe(
        [("setup", "PREPARE"), ("heat", "HEAT"), ("release", "UNLOAD")], count=1
    )
    op(result, "setup")["description"] = "设置设备程序/补水；合成测试"
    result["operation_metadata"]["release"] = {"material_handling": "NONE"}
    attach_materials(
        result,
        entry([{"step": 1, "inputs": ["raw_001"], "outputs": [output("cooked")]}], ["cooked"]),
    )
    for name in ("setup", "release"):
        assert not op(result, name)["material_inputs"]
        assert not op(result, name)["material_outputs"]
    assert op(result, "heat")["material_outputs"][0]["spec_id"] == "cooked"


def test_fraction_scales_both_quantity_range_bounds():
    result = fixture_recipe([("cut", "CUT")], count=1)
    supply = result["canonical"]["ingredient_requirements"][0]
    supply["quantity_kind"] = "RANGE"
    supply["upper_quantity"] = {"value": 10, "unit": "g", "scale": 3}
    attach_materials(
        result,
        entry(
            [
                {
                    "step": 1,
                    "inputs": [{"id": "raw_001", "fraction": "2/3"}],
                    "outputs": [output("cut_food")],
                }
            ],
            ["cut_food"],
        ),
    )
    demand = op(result, "cut")["material_inputs"][0]
    assert demand["quantity_kind"] == "RANGE"
    assert demand["quantity"] == {"value": 14, "unit": "g", "scale": 9}
    assert demand["upper_quantity"] == {"value": 20, "unit": "g", "scale": 9}


def test_real_hundred_recipes_references_and_producers_precede_consumers():
    directory = ROOT / "data/revisions/recipes_v3"
    dataset = json.loads((directory / "scheduling_dataset.json").read_text(encoding="utf-8"))
    maps = [
        item
        for path in sorted(directory.glob("material_map_*.json"))
        for item in json.loads(path.read_text(encoding="utf-8"))
    ]
    assert len(maps) == 100
    for mapping in maps:
        result = deepcopy(dataset["recipes"][mapping["row"] - 1])
        attach_materials(result, mapping)
        CanonicalRecipeModel.model_validate(result["canonical"])
        produced = {
            requirement["spec_id"] for requirement in result["canonical"]["ingredient_requirements"]
        }
        for operation in result["canonical"]["operations"]:
            for requirement in operation["material_inputs"]:
                assert requirement["spec_id"] in produced, (
                    mapping["row"],
                    operation["operation_id"],
                )
            for requirement in operation["material_outputs"]:
                assert requirement["spec_id"] not in produced
                produced.add(requirement["spec_id"])
        assert "finaldish" in produced


@pytest.mark.parametrize(
    "selection",
    [
        ["oil_load", "oil_heat"],
        [],
        ["absent"],
        ["preheat"],
        ["outside"],
        ["oil_load", "oil_load"],
        ["oil_heat", "oil_load"],
    ],
)
def test_explicit_stage_operations_keep_fish_out_of_hot_oil(selection):
    result = fixture_recipe(
        [
            ("preheat", "PREHEAT"),
            ("fish", "UNLOAD"),
            ("oil_load", "LOAD"),
            ("oil_heat", "HEAT"),
            ("combine", "ADD"),
            ("outside", "CUT"),
        ]
    )
    result["source_groups"] = {
        "1": ["preheat", "fish", "oil_load", "oil_heat", "combine"],
        "2": ["outside"],
    }
    result["canonical"]["dependencies"] = [
        {
            "predecessor_id": "oil_load",
            "successor_id": "oil_heat",
            "reason": "先加油再加热",
            "evidence_refs": ["synthetic:test"],
        }
    ]
    mapping = entry(
        [
            {
                "step": 1,
                "operation_ids": ["fish"],
                "inputs": ["raw_001"],
                "outputs": [output("fish_out")],
            },
            {
                "step": 1,
                "operation_ids": selection,
                "inputs": ["raw_002"],
                "outputs": [output("hot_oil")],
            },
            {
                "step": 1,
                "operation_ids": ["combine"],
                "inputs": ["fish_out", "hot_oil"],
                "outputs": [output("dish")],
            },
        ],
        ["dish"],
    )
    if selection != ["oil_load", "oil_heat"]:
        before = deepcopy(result)
        with pytest.raises(ValueError):
            attach_materials(result, mapping)
        assert result == before
        return
    attach_materials(result, mapping)
    specs = {spec["spec_id"]: spec for spec in result["canonical"]["material_specs"]}
    assert op(result, "oil_heat")["material_outputs"][0]["spec_id"] == "hot_oil"
    for requirement in op(result, "oil_heat")["material_inputs"]:
        assert specs[requirement["spec_id"]]["composition"] == ["ingredient_002"]
    assert {item["spec_id"] for item in op(result, "combine")["material_inputs"]} == {
        "fish_out",
        "hot_oil",
    }


def test_hot_oil_and_sauce_do_not_heat_the_plated_main_food():
    data = json.loads(
        (ROOT / "data/revisions/recipes_v3/scheduling_dataset.json").read_text(encoding="utf-8")
    )
    for row, steps in [
        (9, {5, 8}),
        (10, {8}),
        (11, {10}),
        (19, {5}),
        (26, {5}),
        (43, {5, 8}),
        (46, {7}),
    ]:
        result = data["recipes"][row - 1]
        specs = {s["spec_id"]: s for s in result["canonical"]["material_specs"]}
        heats = [
            o
            for o in result["canonical"]["operations"]
            if o["action"] == "HEAT"
            and result["operation_metadata"][o["operation_id"]]["source_step"] in steps
        ]
        assert heats
        for operation in heats:
            for requirement in operation["material_inputs"]:
                spec = specs[requirement["spec_id"]]
                assert "ingredient_001" not in [spec["spec_id"], *spec["composition"]], (
                    row,
                    operation["operation_id"],
                )
