"""100 个明确合成 ID 验证正式发布机制；不修改任何真实审核记录。"""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.domain.canonical_recipe import CanonicalRecipeModel
from app.domain.knowledge import MenuKnowledgeView, ReleaseRef
from app.domain.release_feasibility import ReleasePlanProof
from app.knowledge.index import build_index
from app.knowledge.loader import load_release
from app.pipeline.publish import publish_release
from app.scheduling.cp_sat import CpSatScheduler
from app.validation.knowledge import validate_knowledge
from app.validation.schedule import ScheduleValidator
from tests.compiler_support import runtime
from tests.integration.test_release_activation import bundle
from tests.unit.test_cp_sat_model import deadline
from tests.unit.test_problem_compilation import compile_menu


def test_full_synthetic_release_requires_and_packages_revalidated_single_plans(tmp_path):
    original = bundle(tmp_path / "synthetic-input", "synthetic-competition")
    source = original.build.snapshot.knowledge
    recipes = []
    for index in range(100):
        payload = source.recipes[0].model_dump(mode="json")
        payload["recipe_id"] = f"synthetic-{index}"
        draft = CanonicalRecipeModel.model_validate(payload)
        payload.update(
            review_status="APPROVED",
            approval={
                "review_id": f"synthetic-approval-{index}",
                "reviewer": "synthetic-tester",
                "reviewed_at": datetime.now(UTC).isoformat(),
                "evidence_refs": ["synthetic-source"],
                "approved_content_hash": draft.semantic_hash(),
            },
        )
        recipes.append(CanonicalRecipeModel.model_validate(payload))
    scope = source.scope.model_copy(
        update={
            "release_kind": "competition",
            "expected_recipe_ids": tuple(r.recipe_id for r in recipes),
        }
    )
    check = validate_knowledge(tuple(recipes), source.profiles, source.rules, scope)
    assert check.valid, check.violations
    source = source.model_copy(
        update={"recipes": tuple(recipes), "scope": scope, "validation": check}
    )
    snapshot = original.build.snapshot.model_copy(
        update={
            "snapshot_id": "snapshot-" + source.content_hash,
            "content_hash": source.content_hash,
            "knowledge": source,
        }
    )
    build = original.build.model_copy(
        update={
            "snapshot": snapshot,
            "index": build_index(source),
            "validation": original.build.validation.model_copy(
                update={
                    "canonical_hash": source.content_hash,
                    "snapshot_hash": snapshot.content_hash,
                }
            ),
        }
    )
    candidate_bundle = replace(original, build=build)
    with pytest.raises(ValueError, match="计划"):
        publish_release(candidate_bundle, tmp_path / "releases")
    view = MenuKnowledgeView(
        release=ReleaseRef(
            release_id=source.release_id,
            knowledge_version=source.knowledge_version,
            rule_version=scope.rule_version,
            snapshot_id=snapshot.snapshot_id,
            manifest_hash="0" * 64,
            release_kind="competition",
        ),
        snapshot_schema_version=snapshot.snapshot_schema_version,
        snapshot_hash=snapshot.content_hash,
        recipes=tuple(recipes),
        devices=scope.devices,
        profiles=source.profiles,
        rules=source.rules,
        provenance_index=build.index.evidence,
        device_choices=scope.device_choices,
    )
    state = runtime(view)
    proofs = []
    for recipe in recipes:
        problem = compile_menu(recipe, knowledge=view, state=state)
        solved = CpSatScheduler().solve(problem, None, deadline())
        assert solved.candidate is not None
        proof = ScheduleValidator().validate(view, state, problem, solved.candidate)
        proofs.append(
            ReleasePlanProof(problem=problem, candidate=solved.candidate, validation=proof)
        )
    ref = publish_release(
        replace(candidate_bundle, single_recipe_plans=tuple(proofs)), tmp_path / "releases"
    )
    loaded = load_release(tmp_path / "releases", ref)
    assert loaded.manifest.release_kind == "competition"
    assert any(a.path == "single_recipe_plans.json" for a in loaded.manifest.artifacts)
