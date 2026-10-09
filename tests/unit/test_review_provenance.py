import hashlib
import json
from datetime import datetime

import pytest
from pydantic import ValidationError

from app.domain.extraction import ExtractionRun
from app.domain.provenance import ArtifactRef, ProvenanceRecord
from app.domain.review import ReviewPatch, apply_review_patch
from tests.helpers import recipe

AT = datetime.fromisoformat("2026-09-22T12:00:00+08:00")


def approval_patch(value, **overrides):
    params = dict(
        patch_id="review-1",
        recipe_id=value.recipe_id,
        base_recipe_version=value.recipe_version,
        base_content_hash=value.semantic_hash(),
        action="APPROVE",
        actor_kind="HUMAN",
        reviewer="合成测试审核员",
        reviewed_at=AT,
        reason="合成测试证据",
        evidence_refs=("synthetic-source",),
    )
    params.update(overrides)
    return ReviewPatch(**params)


def test_model_cannot_self_approve():
    value = recipe()
    with pytest.raises(ValidationError):
        approval_patch(value, actor_kind="MODEL")
    with pytest.raises(ValidationError):
        approval_patch(value, reviewer="")
    with pytest.raises(ValidationError):
        approval_patch(value, evidence_refs=())


def test_change_invalidates_previous_approval_without_losing_history():
    initial = recipe()
    approved = apply_review_patch(initial, approval_patch(initial))
    assert approved.review_status == "APPROVED"
    patch = approval_patch(
        approved,
        patch_id="review-2",
        action="MODIFY",
        next_recipe_version="2",
        changes=(
            {
                "path": "/operations/0/duration/execution_sec",
                "before_json": "60",
                "after_json": "120",
            },
        ),
    )
    updated = apply_review_patch(approved, patch)
    assert updated.review_status == "NEEDS_REVIEW"
    assert updated.approval is None
    assert updated.review_patch_refs == ("review-1", "review-2")
    assert approved.operations[0].duration.execution_sec == 60
    assert updated.operations[0].duration.execution_sec == 120
    with pytest.raises(ValueError, match="版本|内容"):
        apply_review_patch(updated, patch)


def test_review_patch_cannot_overwrite_identity_or_forge_before_value():
    value = recipe()
    for path, before, after in [
        ("/recipe_id", '"synthetic-recipe"', '"new"'),
        ("/operations/0/duration/execution_sec", "999", "120"),
    ]:
        patch = approval_patch(
            value,
            action="MODIFY",
            next_recipe_version="2",
            changes=({"path": path, "before_json": before, "after_json": after},),
        )
        with pytest.raises(ValueError):
            apply_review_patch(value, patch)


def test_artifact_must_exist_and_match_hash(tmp_path):
    path = tmp_path / "原文.txt"
    path.write_text("菜谱原文", encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    ref = ArtifactRef(path="原文.txt", sha256=digest, media_type="text/plain")
    assert ref.verify(tmp_path) == path
    path.write_text("被修改", encoding="utf-8")
    with pytest.raises(ValueError):
        ref.verify(tmp_path)
    path.unlink()
    with pytest.raises(ValueError):
        ref.verify(tmp_path)
    with pytest.raises(ValidationError):
        ArtifactRef(path="../escape", sha256=digest, media_type="text/plain")


def test_extraction_runs_keep_identity_and_actual_content(tmp_path):
    path = tmp_path / "artifact.json"
    path.write_text(json.dumps({"input": "原文"}), encoding="utf-8")
    artifact = ArtifactRef(
        path="artifact.json",
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        media_type="application/json",
    )
    inputs = dict(
        source_recipe_id="synthetic-recipe",
        source=artifact,
        rendered_request=artifact,
        output_schema=artifact,
        raw_response=artifact,
        provider="synthetic",
        requested_model="fixed-test-double",
        prompt_version="v1",
        schema_version="1",
        parser_version="1",
        normalizer_version="1",
        rule_version="1",
        code_revision="test-only",
        dependency_lock_hash=artifact.sha256,
        called_at=AT,
        status="FAILED",
        error_message="合成失败调用",
    )
    a, b = ExtractionRun(**inputs), ExtractionRun(**inputs)
    assert a.run_id != b.run_id
    assert a.source.sha256 == b.source.sha256
    a.verify_artifacts(tmp_path)
    path.unlink()
    with pytest.raises(ValueError):
        a.verify_artifacts(tmp_path)


def test_confidence_is_not_review_authority():
    with pytest.raises(ValidationError):
        ProvenanceRecord(
            provenance_id="p",
            origin="MODEL_SUGGESTION",
            source_file="source.txt",
            source_hash="0" * 64,
            record_id="r",
            field_path="/duration",
            text_span="原文",
            confidence=0.99,
            confidence_kind="HEURISTIC",
            review_status="APPROVED",
        )


def test_approval_cannot_hide_identity_change_inside_container():
    value = recipe()
    before = value.model_dump(mode="json")["operations"]
    after = json.loads(json.dumps(before))
    after[0]["provenance_refs"] = ["forged-source"]
    patch = approval_patch(
        value,
        action="MODIFY",
        next_recipe_version="2",
        changes=(
            {
                "path": "/operations",
                "before_json": json.dumps(before),
                "after_json": json.dumps(after),
            },
        ),
    )
    with pytest.raises(ValueError, match="身份|来源"):
        apply_review_patch(value, patch)
