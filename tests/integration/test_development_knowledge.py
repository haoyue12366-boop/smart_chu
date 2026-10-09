"""现存 V3 的真实离线知识检查；与人工审核和求解验收分别报告。"""

from pathlib import Path

from app.pipeline.development import load_development_knowledge
from app.validation.knowledge import validate_knowledge

ROOT = Path(__file__).resolve().parents[2]


def test_original_v3_hot_water_mapping_error_is_not_hidden_by_development_mode():
    knowledge = load_development_knowledge(
        ROOT, "data/development/development-v3-rebased-v1/dataset.json"
    )
    assert len(knowledge.recipes) == 100
    assert knowledge.scope.release_kind == "development"
    assert len(knowledge.provenance) >= 1189
    report = validate_knowledge(knowledge.recipes, knowledge.profiles, (), knowledge.scope)
    assert not report.valid
    assert len(report.usable_recipe_ids) == 99
    assert [(v.code, v.entity_refs) for v in report.violations] == [
        ("DEVICE_PATH_INVALID", ("58e70ae1a3fd4a750f4b75b0", "op_002_02"))
    ]
    assert not report.formal_release_eligible


def test_v3_minute_format_is_honestly_reported_as_incompatible():
    knowledge = load_development_knowledge(ROOT)
    report = validate_knowledge(
        knowledge.recipes,
        knowledge.profiles,
        (),
        knowledge.scope.model_copy(update={"time_grid_sec": 60}),
    )
    assert not report.valid
    assert any(v.code == "TIME_GRID_INCOMPATIBLE" for v in report.violations)
    assert not report.formal_release_eligible
