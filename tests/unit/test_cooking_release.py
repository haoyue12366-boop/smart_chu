"""真实100菜发布绑定与默认目标；不以合成路径替代正式来源。"""

import json
from pathlib import Path

from app.config import AppSettings
from app.domain.policy import SchedulingPolicy
from app.knowledge.loader import load_release, read_release_ref

ROOT = Path(__file__).resolve().parents[2]


def test_default_release_has_reviewed_cooking_boundaries_for_all_100_recipes():
    settings = AppSettings()
    policy = SchedulingPolicy.model_validate_json(settings.policy_path.read_bytes())
    assert policy.objective.spread_basis == "COOKING_FINISH"
    assert policy.objective.stages == ("SPREAD", "HUMAN_BUSY", "MAKESPAN")
    assert policy.objective.spread_target_sec == 300
    release = load_release(
        settings.release_root, read_release_ref(settings.release_root, settings.release_id)
    )
    source = release.snapshot.knowledge
    assert len(source.recipes) == len(source.scope.recipe_contexts) == 100
    contexts = {c.recipe_id: c for c in source.scope.recipe_contexts}
    for recipe in source.recipes:
        rule = contexts[recipe.recipe_id].cooking_completion
        assert rule is not None
        operations = {op.operation_id: op for op in recipe.operations}
        assert all(
            op in operations and operations[op].action != "FINISH" for op in rule.operation_ids
        )
    # 热油不改变主鱼出锅，月饼不把半小时冷却当出锅，奶茶覆盖三个热加工分支。
    expected = {
        "58e70ae1a3fd4a750f4b75af": ["op_008_01"],
        "5f5ecdaadad9417a1b793a9f": ["op_012_01"],
        "65795214c458a177d8438b22": ["op_8000_01", "op_8001_01", "op_8002_01"],
        "5dad41646601a865649f9c06": ["op_8001_01", "op_8002_01"],
    }
    for recipe_id, ids in expected.items():
        ctx = next(c for c in source.scope.recipe_contexts if c.recipe_id.root == recipe_id)
        assert [op.root for op in ctx.cooking_completion.operation_ids] == ids
    audit = json.loads(
        (ROOT / "data/preparations/cook-finish-v1/review.json").read_text(encoding="utf-8")
    )
    assert audit["reviewer_kind"] == "DELEGATED_AGENT"
    assert len(audit["recipes"]) == 100


def test_canonical_export_roundtrip_explicitly_records_no_live_graph_read():
    from app.pipeline.snapshot_export import export_canonical_snapshot

    settings = AppSettings()
    old = "delegated-v3-p4-preparation-v1-all"
    source = load_release(
        settings.release_root, read_release_ref(settings.release_root, old)
    ).snapshot.knowledge
    build = export_canonical_snapshot(source)
    assert build.snapshot.knowledge == source
    assert build.snapshot.exporter_version == "snapshot-canonical-v1"
    assert build.validation.source_kind == "CANONICAL"
