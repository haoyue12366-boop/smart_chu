"""真实现存 V3 的开发重建；损坏例子仅在临时目录生成。"""

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from scripts.build_development_version import VERSION, build_development_version

ROOT = Path(__file__).resolve().parents[2]
V3 = "data/revisions/recipes_v3/scheduling_dataset.json"
CSV = "data/revisions/recipes_v3/recipes_100_调度版.csv"


def copy_inputs(tmp_path):
    for name in (V3, CSV, "docs/recipes_100.csv"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, path)
    return tmp_path


def test_rebase_preserves_all_semantics_and_records_missing_history(tmp_path):
    original = json.loads((ROOT / V3).read_text("utf-8"))
    before = hashlib.sha256((ROOT / V3).read_bytes()).hexdigest()
    report = build_development_version(ROOT, tmp_path)
    rebased = json.loads((tmp_path / "dataset.json").read_text("utf-8"))
    assert rebased["knowledge_version"] == VERSION
    assert report["recipe_count"] == 100
    assert report["operation_count"] == 1564
    assert report["approved_count"] == 0
    assert report["formal_release_eligible"] is False
    assert {g["status"] for g in report["provenance_gaps"]} >= {"MISSING"}
    assert any("时间补全版.json" in g["path"] for g in report["provenance_gaps"])
    for old, new in zip(original["recipes"], rebased["recipes"], strict=True):
        for field in ("canonical", "source_record", "resource_reservations", "program_constraints"):
            assert old[field] == new[field]
        assert new["canonical"]["review_status"] == "NEEDS_REVIEW"
    for name, digest in rebased["source_hashes"].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest
    assert hashlib.sha256((ROOT / V3).read_bytes()).hexdigest() == before
    assert build_development_version(ROOT, tmp_path) == report


@pytest.mark.parametrize("change", ["identity", "duration", "approval"])
def test_inconsistent_csv_or_forged_authority_is_rejected_before_output(tmp_path, change):
    root = copy_inputs(tmp_path / "root")
    path = root / V3
    data = json.loads(path.read_text("utf-8"))
    if change == "identity":
        data["recipes"][0]["name"] = "伪造名称"
    elif change == "duration":
        data["recipes"][0]["canonical"]["operations"][0]["duration"]["execution_sec"] += 1
    else:
        data["approved_count"] = 1
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError):
        build_development_version(root, tmp_path / "output")
    assert not (tmp_path / "output/dataset.json").exists()
