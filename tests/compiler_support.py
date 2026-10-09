"""P2 从真实已发布开发快照取菜；合成运行事件必须在各测试中标注。"""

import json
import os
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from app.domain.runtime_snapshot import RuntimeSnapshot
from app.domain.scheduling_problem import RecipeInstance
from app.domain.time import TimeOrigin
from app.knowledge.loader import read_release_ref
from app.knowledge.repository import SnapshotKnowledgeRepository

ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def published_knowledge():
    # Optional identical-content location for hosts where the immutable original
    # directory is unreadable. The active snapshot identity remains authoritative.
    override = os.environ.get("SMART_COOKING_TEST_RELEASE_ROOT")
    root = Path(override) if override else ROOT / "data/releases"
    ref = read_release_ref(root, "development-v3-rebased-v2-all")
    if override:
        expected = json.loads((ROOT / "data/releases/active_release.json").read_bytes())
        if ref.snapshot_id != expected["snapshot_id"] or ref.release_id != expected["release_id"]:
            raise ValueError("回归副本不是原运行快照，不能替换基准")
    repository = SnapshotKnowledgeRepository(root)
    repository.load(ref)
    with repository.acquire(ref) as lease:
        return lease.select(tuple(r.recipe_id for r in lease.loaded.snapshot.knowledge.recipes))


def runtime(knowledge=None, **updates):
    knowledge = knowledge or published_knowledge()
    return RuntimeSnapshot(
        session_id="p2-development-test",
        state_revision=0,
        current_plan_version=0,
        knowledge_version=knowledge.release.knowledge_version,
        rule_version=knowledge.release.rule_version,
        snapshot_id=knowledge.release.snapshot_id,
        time_origin=TimeOrigin(start_at=datetime.fromisoformat("2026-09-28T23:59:00+08:00")),
        now_offset_sec=0,
        execution_mode="SIMULATED",
    ).model_copy(update=updates)


def menu_for(*recipes):
    return tuple(
        RecipeInstance(recipe_instance_id=f"instance-{i}", recipe_id=r.recipe_id, name=r.name)
        for i, r in enumerate(recipes)
    )
