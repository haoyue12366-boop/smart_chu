"""代理审核不得改变工艺，也不能用不匹配的授权或证据批准。"""

import copy
from pathlib import Path

import pytest

from app.knowledge.loader import load_release, read_release_ref
from scripts.prepare_p4 import approve_recipes, audit_release

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def base():
    root = ROOT / "data/preparations/p3-thermal-v1/releases"
    return load_release(root, read_release_ref(root, "development-v3-p3-thermal-v1-all"))


def authorization(base):
    return {
        "authorization_id": "p4-delegated-review-2026-09-29",
        "source": "USER_MESSAGE",
        "actor_kind": "DELEGATED_AGENT",
        "reviewer": "Codex（用户授权代理审核）",
        "recorded_at": "2026-09-29T16:30:00+08:00",
        "approve_for_p4": True,
        "base_content_hash": base.snapshot.knowledge.content_hash,
        "user_instruction": "数据全权授予你代我审核通过即可",
    }


def test_approval_is_bound_to_unchanged_process_and_named_delegate(base):
    audit = audit_release(base)
    recipes = approve_recipes(base, authorization(base), audit)
    assert len(recipes) == 100
    for before, after in zip(base.snapshot.knowledge.recipes, recipes, strict=True):
        assert after.review_status == "APPROVED"
        assert after.approval.reviewer == "Codex（用户授权代理审核）"
        assert before.semantic_hash() == after.semantic_hash()
        assert before.operations == after.operations
    assert audit["marinate_count"] == 28
    assert audit["marinate_human_conflicts"] == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("approve_for_p4", False),
        ("actor_kind", "HUMAN"),
        ("base_content_hash", "0" * 64),
        ("source", "MODEL_INFERENCE"),
    ],
)
def test_invalid_authority_cannot_approve(base, field, value):
    auth = authorization(base)
    auth[field] = value
    with pytest.raises(ValueError):
        approve_recipes(base, auth, audit_release(base))


def test_changed_or_failed_audit_cannot_approve(base):
    audit = audit_release(base)
    for field, value in (("valid", False), ("base_content_hash", "0" * 64)):
        changed = copy.deepcopy(audit)
        changed[field] = value
        with pytest.raises(ValueError):
            approve_recipes(base, authorization(base), changed)
