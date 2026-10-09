"""100 道使用真实 PlanningCore；开发证据始终不等同于人工批准。"""

import json
import os
from pathlib import Path

import pytest

from scripts.verify_all_recipes import verify_all
from tests.compiler_support import ROOT


def test_all_100_development_recipes_have_validated_plan(tmp_path):
    output = Path(os.environ.get("SMART_COOKING_VERIFY_ALL_OUTPUT", str(tmp_path)))
    report = verify_all(ROOT / "data/releases", "development-v3-rebased-v2-all", output)
    assert report["recipe_count"] == 100
    assert report["success_count"] == 100, [
        r for r in report["results"] if r["status"] != "VALIDATED"
    ]
    assert not report["formal_release_eligible"]
    assert report["review_pending_count"] == 100
    assert len({r["recipe_id"] for r in report["results"]}) == 100
    assert all(r["plan_artifact"] and r["problem_hash"] for r in report["results"])


@pytest.mark.review_gate
def test_formal_full_gate_requires_reviewed_competition_release():
    from app.domain.knowledge import ReleaseRef
    from app.knowledge.loader import load_release

    root = ROOT / "data/releases"
    ref = ReleaseRef.model_validate(json.loads((root / "active_release.json").read_bytes()))
    assert ref.release_kind == "competition", "100道正式人工审核仍为待办，development不能通过full"
    loaded = load_release(root, ref)
    assert loaded.snapshot.knowledge.validation.formal_release_eligible
    assert len(loaded.snapshot.knowledge.recipes) == 100
    assert any(a.path == "single_recipe_plans.json" for a in loaded.manifest.artifacts)
