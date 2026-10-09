"""准备蓝图必须覆盖完整源程序，特别是人工设置步骤。"""

from functools import lru_cache

from app.knowledge.loader import load_release, read_release_ref
from app.pipeline.p3_preflight import build_preflight
from tests.compiler_support import ROOT


@lru_cache(maxsize=1)
def preflight():
    releases = ROOT / "data/preparations/p3-v1/releases"
    source = load_release(
        releases, read_release_ref(releases, "development-v3-p3-preparation-v1-all")
    ).snapshot.knowledge
    return build_preflight(source)


def test_thermal_blueprint_includes_setup_and_full_outer_reservation():
    report = preflight()
    heat = next(c for c in report["groups"] if c["kind"] == "STRICT_TOGETHER")
    assert heat["phase_seconds"] == {
        "LOAD": 120,
        "PREPARE": 120,
        "PREHEAT": 300,
        "HEAT": 720,
        "UNLOAD": 240,
    }
    assert heat["reservation_sec"] == 1500
    assert len(heat["replaced_operation_refs"]) == 10
    assert (
        sum(x["duration_sec"] for x in heat["source_operations"] if x["action"] == "PREPARE") == 120
    )
    assert heat["runtime_enabled"] is False


def test_prep_ports_remain_separate_and_do_not_invent_mass_yield():
    cut = next(c for c in preflight()["groups"] if c["kind"] == "SHARED_PREP")
    assert cut["duration_sec"] == 240
    assert len(cut["output_ports"]) == 2
    assert len({p["port_id"] for p in cut["output_ports"]}) == 2
    assert all(
        p["quantity"] is None and p["quantity_kind"] == "QUALITATIVE" for p in cut["output_ports"]
    )
    assert cut["mass_yield"] is None
    assert cut["actual_inventory_created"] is False
    assert cut["standalone_preserved"] is True


def test_every_output_has_source_and_real_downstream_consumers():
    for group in preflight()["groups"]:
        for port in group["output_ports"]:
            assert port["producer_operation_id"]
            assert port["recipe_id"]
            assert port["consumers"]
    assert preflight()["planning_result"] == "NOT_RUN"
