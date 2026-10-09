"""设备能力必须来自真实清单，不从某道菜推断设备上限。"""

from pathlib import Path

from app.pipeline.device_import import import_devices
from app.pipeline.device_profiles import build_device_profiles

ROOT = Path(__file__).resolve().parents[2]


def test_all_device_modes_keep_their_own_ranges_and_unknown_program_times():
    source = import_devices(ROOT / "docs/设备参数清单参考.json")
    profiles = build_device_profiles(source, "test-rules")
    by_id = {p.profile_id: p for p in profiles}
    assert {p.device_type for p in profiles} == {d.name for d in source.devices}
    assert all(p.review_status == "NEEDS_REVIEW" and p.provenance_refs for p in profiles)
    steam = {c.parameter: c for c in by_id["蒸箱:普通蒸"].constraints}
    assert (steam["temperature_c"].minimum, steam["temperature_c"].maximum) == (91, 100)
    assert steam["duration_sec"].maximum == 18000
    dry = {c.parameter: c for c in by_id["烤箱:蔬果干"].constraints}
    assert dry["duration_sec"].maximum == 43200
    assert dry["temperature_c"].maximum == 120
    humid = {c.parameter: c for c in by_id["烤箱:加湿烤"].constraints}
    assert humid["humidity"].allowed_values == ("低", "中", "高")
    assert all(c.parameter != "duration_sec" for c in by_id["冰箱:冷藏"].constraints)
    assert all(c.parameter != "duration_sec" for c in by_id["洗碗机:日常洗"].constraints)
    assert all(c.parameter != "duration_sec" for c in by_id["净咖一体机:浓缩"].constraints)
