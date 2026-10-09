"""独立校验器必须拒绝看似完整却违背执行语义的排程。"""

import copy
import json
from pathlib import Path

import pytest

from scripts.check_scheduling_dataset import solve, validate_schedule

PATH = Path(__file__).resolve().parents[2] / "data/revisions/recipes_v3/scheduling_dataset.json"


@pytest.fixture(scope="module")
def solved():
    recipes = json.loads(PATH.read_text(encoding="utf-8"))["recipes"]
    menu = [recipes[74], recipes[90]]
    plan = solve(menu)
    assert plan["tasks"], plan["status"]
    return menu, plan


def test_rejects_missing_operation(solved):
    menu, plan = solved
    bad = copy.deepcopy(plan)
    bad["tasks"].pop()
    with pytest.raises(AssertionError, match="覆盖"):
        validate_schedule(menu, bad)


def test_rejects_duration_changed(solved):
    menu, plan = solved
    bad = copy.deepcopy(plan)
    bad["tasks"][0]["end_sec"] += 1
    with pytest.raises(AssertionError, match="时长"):
        validate_schedule(menu, bad)


def test_rejects_invalid_burner_assignment(solved):
    menu, plan = solved
    bad = copy.deepcopy(plan)
    key = next(iter(bad["resource_assignments"]))
    bad["resource_assignments"][key] = "invented_device"
    with pytest.raises(AssertionError, match="资源选择"):
        validate_schedule(menu, bad)


def test_rejects_double_consumption_of_real_food():
    from scripts.check_scheduling_dataset import check_materials

    item = copy.deepcopy(json.loads(PATH.read_text(encoding="utf-8"))["recipes"][0])
    op = next(o for o in item["canonical"]["operations"] if o["material_inputs"])
    op["material_inputs"].extend(copy.deepcopy(op["material_inputs"]))
    with pytest.raises(AssertionError, match="超用"):
        check_materials(item)


def test_rejects_food_without_dependency_path():
    from scripts.check_scheduling_dataset import check_materials

    item = copy.deepcopy(json.loads(PATH.read_text(encoding="utf-8"))["recipes"][0])
    item["canonical"]["dependencies"] = []
    with pytest.raises(AssertionError, match="生产者"):
        check_materials(item)
