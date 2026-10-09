"""明确拆步、审核与重放的合成反例，不构成真实菜谱人工审核。"""

import asyncio
import json

import httpx
import pytest

from app.domain.canonical_recipe import CanonicalRecipeModel, OperationTemplate
from app.domain.provenance import ProvenanceRecord
from app.pipeline.normalize import NormalizationEvidence, OperationExpansion, normalize
from app.pipeline.replay import replay_extraction
from app.pipeline.review import apply_archived_review
from tests.helpers import recipe
from tests.integration.test_llm_provider_contract import call_provider, setup_request
from tests.unit.test_review_provenance import approval_patch


def evidence(value, **overrides):
    fields = dict(
        normalizer_version="normalize-v1",
        output_recipe_version="normalized-v1",
        records=(
            ProvenanceRecord(
                provenance_id="synthetic-source",
                origin="MODEL_SUGGESTION",
                source_file="synthetic.txt",
                source_hash="0" * 64,
                record_id=value.recipe_id.root,
                field_path="/operations",
                text_span="合成工艺依据",
            ),
        ),
    )
    fields.update(overrides)
    return NormalizationEvidence(**fields)


def test_normalization_preserves_interventions_unloads_and_fixed_batches():
    value = recipe()
    result = normalize(value, evidence(value))
    assert result.recipe.operations == value.operations
    assert result.recipe.dependencies == value.dependencies
    assert result.recipe.operations[2].execution_policy.interventions == (
        value.operations[2].execution_policy.interventions
    )
    assert result.recipe.review_status == "NEEDS_REVIEW"
    assert result.recipe.approval is None
    assert result.provenance == evidence(value).records


def test_explicit_compound_split_keeps_entry_exit_and_active_wait_boundaries():
    value = recipe()
    original = value.operations[1]
    first = OperationTemplate.model_validate(
        {
            **original.model_dump(),
            "operation_id": "prep_b_active",
            "action": "MIX",
            "duration": {"execution_sec": 60, "source_ref": "synthetic-source"},
            "resource_requirements": [
                {"resource_type": "HUMAN", "resource_id": "human_1", "conflict_policy": "UNARY"}
            ],
        }
    )
    second = OperationTemplate.model_validate(
        {
            **original.model_dump(),
            "operation_id": "prep_b_wait",
            "duration": {"execution_sec": 60, "source_ref": "synthetic-source"},
        }
    )
    result = normalize(
        value,
        evidence(
            value,
            expansions=(
                OperationExpansion(
                    source_operation_id=original.operation_id,
                    phases=(first, second),
                    evidence_refs=("synthetic-source",),
                ),
            ),
        ),
    )
    assert len(result.recipe.operations) == len(value.operations) + 1
    assert {o.operation_id.root for o in result.recipe.operations} >= {
        "prep_b_active",
        "prep_b_wait",
    }
    assert any(
        d.predecessor_id.root == "prep_b_active"
        and d.successor_id.root == "prep_b_wait"
        and d.max_lag_sec == 0
        for d in result.recipe.dependencies
    )
    assert any(
        d.predecessor_id.root == "prep_b_wait" and d.successor_id.root == "steam_1"
        for d in result.recipe.dependencies
    )


def test_split_does_not_silently_drop_intervention_anchor_or_change_duration():
    value = recipe()
    original = value.operations[2]
    altered = original.model_dump()
    altered["operation_id"] = "new-heat"
    altered["duration"]["execution_sec"] = 300
    phase = OperationTemplate.model_validate(altered)
    with pytest.raises(ValueError, match="介入|时长"):
        normalize(
            value,
            evidence(
                value,
                expansions=(
                    OperationExpansion(
                        source_operation_id=original.operation_id,
                        phases=(phase,),
                        evidence_refs=("synthetic-source",),
                    ),
                ),
            ),
        )


def test_unknown_time_and_out_of_range_temperature_are_not_clipped():
    value = recipe()
    payload = value.model_dump(mode="json")
    payload["operations"][0]["duration"]["execution_sec"] = None
    payload["operations"][2]["resource_requirements"] = [
        {
            "resource_type": "DEVICE",
            "resource_id": "oven",
            "conflict_policy": "BATCH_EXCLUSIVE",
            "configuration": [{"parameter": "temperature", "value": 240}],
        }
    ]
    value = CanonicalRecipeModel.model_validate(payload)
    result = normalize(value, evidence(value))
    assert result.recipe.operations[0].duration.execution_sec is None
    assert result.recipe.operations[2].resource_requirements[0].configuration[0].value == 240
    assert "MISSING_DURATION" in {issue.code for issue in result.issues}


def test_missing_provenance_stays_blocking_and_estimates_do_not_become_measured():
    value = recipe()
    result = normalize(value, evidence(value, records=()))
    assert "MISSING_PROVENANCE" in {issue.code for issue in result.issues}
    assert all(issue.severity == "BLOCKING" for issue in result.issues)
    result = normalize(value, evidence(value))
    assert result.provenance[0].origin == "MODEL_SUGGESTION"


def test_archive_review_and_replay_require_evidence_and_every_patch(tmp_path):
    archive, request, payload, versions = setup_request(tmp_path)
    extraction = asyncio.run(
        call_provider(
            archive,
            request,
            versions,
            httpx.Response(200, json={"finish": "done", "data": {"recipe": payload}}),
        )
    )
    draft = replay_extraction(extraction.run_id, versions, archive).recipe
    review_evidence = archive.put(b"SYNTHETIC HUMAN REVIEW ONLY", "text/plain")
    patch = approval_patch(
        draft,
        action="MODIFY",
        next_recipe_version="edited-v1",
        changes=(
            {
                "path": "/operations/0/duration/execution_sec",
                "before_json": "60",
                "after_json": "120",
            },
        ),
    )
    with pytest.raises(ValueError, match="审核依据"):
        apply_archived_review(draft, patch, archive, {})
    modified = apply_archived_review(draft, patch, archive, {"synthetic-source": review_evidence})
    approval = approval_patch(modified.recipe, patch_id="approval-after-edit")
    final = apply_archived_review(
        modified.recipe, approval, archive, {"synthetic-source": review_evidence}
    )
    refs = (modified.patch_ref, final.patch_ref)
    expected = final.recipe.document_hash
    report = replay_extraction(
        extraction.run_id, versions, archive, review_patch_refs=refs, expected_content_hash=expected
    )
    assert report.recipe == final.recipe
    assert report == replay_extraction(
        extraction.run_id, versions, archive, review_patch_refs=refs, expected_content_hash=expected
    )
    with pytest.raises(ValueError, match="哈希|补丁"):
        replay_extraction(extraction.run_id, versions, archive, expected_content_hash=expected)
    (tmp_path / modified.patch_ref.path).unlink()
    with pytest.raises(ValueError, match="不存在"):
        replay_extraction(extraction.run_id, versions, archive, review_patch_refs=refs)


def test_unit_normalization_is_exact_and_leaves_qualitative_amounts(tmp_path):
    value = recipe()
    payload = value.model_dump(mode="json")
    payload["material_specs"] = [
        {"spec_id": "oil", "ingredient_id": "oil", "name": "油", "state": "raw"}
    ]
    payload["ingredient_requirements"] = [
        {
            "requirement_id": "oil-in",
            "spec_id": "oil",
            "quantity_kind": "EXACT",
            "quantity": {"value": 1, "scale": 8, "unit": "kg"},
        }
    ]
    value = CanonicalRecipeModel.model_validate(payload)
    result = normalize(value, evidence(value))
    quantity = result.recipe.ingredient_requirements[0].quantity
    assert quantity.unit == "g" and quantity.value == 125 and quantity.scale == 1
    assert value.ingredient_requirements[0].quantity.unit == "kg"
    assert json.loads(result.recipe.model_dump_json())["approval"] is None


def test_replay_checks_normalizer_version_and_archived_review_evidence(tmp_path):
    archive, request, payload, versions = setup_request(tmp_path)
    extraction = asyncio.run(
        call_provider(
            archive,
            request,
            versions,
            httpx.Response(200, json={"finish": "done", "data": {"recipe": payload}}),
        )
    )
    draft = replay_extraction(extraction.run_id, versions, archive).recipe
    normalization = evidence(draft)
    normalization_ref = archive.put(normalization.model_dump_json().encode(), "application/json")
    result = normalize(draft, normalization)
    replayed = replay_extraction(
        extraction.run_id, versions, archive, normalization_ref=normalization_ref
    )
    assert result.recipe == replayed.recipe
    altered_versions = versions.model_copy(update={"normalizer_version": "unsupported"})
    with pytest.raises(ValueError, match="版本"):
        replay_extraction(
            extraction.run_id, altered_versions, archive, normalization_ref=normalization_ref
        )
    review_evidence = archive.put(b"SYNTHETIC REVIEW EVIDENCE", "text/plain")
    reviewed = apply_archived_review(
        result.recipe, approval_patch(result.recipe), archive, {"synthetic-source": review_evidence}
    )
    (tmp_path / review_evidence.path).unlink()
    with pytest.raises(ValueError, match="不存在"):
        replay_extraction(
            extraction.run_id,
            versions,
            archive,
            normalization_ref=normalization_ref,
            review_patch_refs=(reviewed.patch_ref,),
        )
