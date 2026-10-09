"""真实原文导入与明确标注的损坏输入反例。"""

import csv
import hashlib
import json
from pathlib import Path

import pytest

from app.pipeline.device_import import import_devices
from app.pipeline.import_raw import SourceImportError, import_recipes

ROOT = Path(__file__).resolve().parents[2]
HEADERS = ["菜谱id", "名称", "食材清单", "烹饪步骤"]


def write_csv(path, rows, encoding="utf-8-sig"):
    with path.open("w", encoding=encoding, newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(HEADERS)
        writer.writerows(rows)


def test_import_original_all_ids_steps_and_stable_hash():
    source = ROOT / "docs/recipes_100.csv"
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    recipes = import_recipes(source)
    assert len(recipes) == len({r.recipe_id for r in recipes}) == 100
    assert len({r.name for r in recipes}) == 99
    assert len({r.recipe_id for r in recipes if r.name == "麻辣对虾"}) == 2
    assert sum(len(r.steps) for r in recipes) == 762
    assert all(r.source_sha256 == before for r in recipes)
    assert import_recipes(source) == recipes
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    for recipe in recipes:
        assert recipe.steps
        for step in recipe.steps:
            assert recipe.steps_text[step.start_offset : step.end_offset] == step.text
    assert any("屏幕" in r.steps_text for r in recipes)


def test_v3_is_distinct_source_without_reinterpreting_raw_step_count():
    path = ROOT / "data/revisions/recipes_v3/recipes_100_调度版.csv"
    recipes = import_recipes(path)
    assert len(recipes) == 100
    assert sum(len(r.steps) for r in recipes) == 1564
    assert "op_001_01" in recipes[0].steps_text
    assert "AI" in recipes[0].steps_text


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-8", "gb18030"])
def test_chinese_paths_quotes_newlines_and_units_preserved(tmp_path, encoding):
    source = tmp_path / "有空格 的菜谱.csv"
    text = "第1步：清洗“菜”，备用。\n第2步：按屏幕提示，加入1/2汤匙油。"
    write_csv(source, [["synthetic-1", "合成菜", "油1/2汤匙\n水2ml", text]], encoding)
    recipe = import_recipes(source)[0]
    assert recipe.steps_text == text
    assert recipe.ingredients_text == "油1/2汤匙\n水2ml"
    assert recipe.row_number == 1
    assert recipe.line_start == 2 and recipe.line_end == 4
    assert len(recipe.steps) == 2
    assert recipe.raw_record[3].value == text


@pytest.mark.parametrize(
    "rows",
    [
        [["id", "name", "ingredients"]],
        [["id", "name", "ingredients", "第1步：处理", "extra"]],
        [["", "name", "ingredients", "第1步：处理"]],
        [["id", "name", "ingredients", ""]],
        [["id", "name", "ingredients", "第1步：处理"]] * 2,
        [["id", "name", "ingredients", "第1步：处理。第3步：完成"]],
    ],
)
def test_corrupt_records_fail_with_location(tmp_path, rows):
    path = tmp_path / "broken.csv"
    write_csv(path, rows)
    with pytest.raises(SourceImportError, match="broken.csv.*行"):
        import_recipes(path)


@pytest.mark.parametrize(
    "text",
    [
        "菜谱id,名称,食材清单,名称\na,b,c,d\n",
        '菜谱id,名称,食材清单,烹饪步骤\na,b,c,"unterminated\n',
        "菜谱id,名称,食材清单,烹饪步骤\n",
        "菜谱id,名称,食材清单,烹饪步骤\na,b,c,无编号原文\n\n",
    ],
)
def test_invalid_header_empty_file_broken_quotes_or_blank_record_rejected(tmp_path, text):
    path = tmp_path / "broken.csv"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(SourceImportError):
        import_recipes(path)


def test_unnumbered_text_is_preserved_without_invented_operations(tmp_path):
    path = tmp_path / "plain.csv"
    write_csv(path, [["synthetic", "合成菜", "适量", "按屏幕提示操作"]])
    recipe = import_recipes(path)[0]
    assert recipe.steps_text == "按屏幕提示操作"
    assert recipe.steps == ()


def test_device_source_preserves_all_ranges_and_missing_program_durations():
    source = ROOT / "docs/设备参数清单参考.json"
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    document = import_devices(source)
    assert len(document.devices) == 7
    assert document.source_sha256 == before
    assert json.loads(document.raw_json) == json.loads(source.read_text("utf-8"))
    dishwasher = next(d for d in document.devices if d.name == "洗碗机")
    assert len(dishwasher.parameters) == 1
    assert dishwasher.parameters[0].choices == ("日常洗", "母婴洗", "超净洗")
    oven = next(d for d in document.devices if d.name == "烤箱")
    humid = next(v for v in oven.parameters[0].variants if v.name == "加湿烤")
    assert dict((f.name, f.value) for f in humid.fields)["湿度"] == "低/中/高"
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


@pytest.mark.parametrize(
    "payload",
    [
        {"设备清单": []},
        {"设备清单": [{"设备名称": "合成设备", "参数": [{"参数名": "x", "类型": "未知"}]}]},
        {"设备清单": [{"设备名称": "合成设备", "参数": []}] * 2},
    ],
)
def test_malformed_devices_are_not_silently_skipped(tmp_path, payload):
    path = tmp_path / "broken.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(SourceImportError):
        import_devices(path)


def test_manifest_binds_original_and_development_artifacts_separately(tmp_path):
    from scripts.import_sources import build_source_manifest

    manifest = build_source_manifest(ROOT, tmp_path)
    assert manifest["status"] == "RAW_IMPORTED_NOT_REVIEWED"
    assert [r["numbered_step_count"] for r in manifest["recipe_sources"]] == [762, 1564]
    for entry in [*manifest["recipe_sources"], manifest["device_source"]]:
        assert (
            entry["source_sha256"]
            == hashlib.sha256((ROOT / entry["source_path"]).read_bytes()).hexdigest()
        )
        assert (
            entry["artifact_sha256"]
            == hashlib.sha256((tmp_path / entry["artifact_path"]).read_bytes()).hexdigest()
        )
    assert manifest == build_source_manifest(ROOT, tmp_path)
