"""设备应在真实卸料时释放，不跨离炉冷藏阶段占用。"""

import json
from pathlib import Path

from scripts.build_scheduling_dataset import action_for, device_for

ROOT = Path(__file__).resolve().parents[2]


def build(row):
    data = json.loads(
        (ROOT / "data/development/development-v3-rebased-v1/dataset.json").read_text(
            encoding="utf-8"
        )
    )
    return data["recipes"][row - 1]


def test_explicit_steam_chamber_wins_over_recipe_oven_default():
    assert (
        device_for(
            {"resource": "", "text": "红豆沙放入方太蒸箱", "kind": "人工", "display_step": 1}, 60
        )
        == "steam"
    )


def test_water_tank_refill_is_not_food_unload():
    assert action_for("将第一批蒸盘放入蒸箱，取出水箱加满水") == "LOAD"


def test_basque_oven_release_precedes_room_cooling_and_refrigeration():
    r = build(97)
    oven = [s for s in r["resource_reservations"] if s["resource_options"] == ["oven_1"]]
    assert len(oven) == 1
    assert "op_007_01" in oven[0]["members"]
    assert not any(m.startswith("op_999") for m in oven[0]["members"])


def test_shaomai_rice_session_releases_before_wrapping_then_reloads():
    r = build(52)
    steam = [s for s in r["resource_reservations"] if s["resource_options"] == ["steam_oven_1"]]
    assert len(steam) >= 2
    first = next(s for s in steam if "op_004_03" in s["members"])
    assert "op_005_01" in first["members"]
    assert "op_020_01" not in first["members"]


def test_fixed_menu_intervention_keeps_one_chamber_reservation():
    for row in (75, 91):
        r = build(row)
        oven = [s for s in r["resource_reservations"] if s["resource_options"] == ["oven_1"]]
        assert len(oven) == 1
        expected = "op_011_01" if row == 75 else "op_023_01"
        assert expected in oven[0]["members"]
        from scripts.check_scheduling_dataset import solve

        assert solve([r])["tasks"]


def test_release_inside_source_group_preserves_dependency_order():
    r = build(91)
    group = r["source_groups"]["3"]
    assert (
        group.index("op_003_01")
        < next(i for i, ident in enumerate(group) if ident.startswith("op_800"))
        < group.index("op_003_02")
    )
