"""仅在临时测试目录重放原P3准备器；旧版本和用户资料不修改。"""

import json
import os
import shutil

from app.knowledge.loader import load_release, read_release_ref
from tests.compiler_support import ROOT


def stage_preparation_root(target):
    baseline_root = ROOT / "data/releases"
    override = os.environ.get("SMART_COOKING_TEST_RELEASE_ROOT")
    if override:
        from pathlib import Path

        baseline_root = Path(override)
    ref = read_release_ref(baseline_root, "development-v3-rebased-v2-all")
    pointer = json.loads((ROOT / "data/releases/active_release.json").read_bytes())
    assert ref.snapshot_id == pointer["snapshot_id"]
    loaded = load_release(baseline_root, ref)
    archive = baseline_root / ref.release_id
    shutil.copytree(archive, target / "data/releases" / ref.release_id)
    for artifact in loaded.snapshot.knowledge.source_artifacts:
        origin = artifact.verify(archive)
        output = target / artifact.path
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origin, output)
    for relative in (
        "data/development/p3-delegated-review-v2.json",
        "data/development/authorizations/p3-delegation-v1.json",
        "data/issues/p3_rule_review_evidence.json",
        "app/pipeline/p3_preparation.py",
    ):
        output = target / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, output)
    return target
