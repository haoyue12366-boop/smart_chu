"""发布可行性证据的合成反例；不为真实开发菜谱伪造人工批准。"""

import pytest

from app.domain.release_feasibility import ReleasePlanProof, validate_plan_bindings
from app.validation.schedule import ScheduleValidator
from tests.unit.test_schedule_validator import example


def proof():
    knowledge, runtime, problem, candidate = example()
    validation = ScheduleValidator().validate(knowledge, runtime, problem, candidate)
    assert validation.valid
    return ReleasePlanProof(problem=problem, candidate=candidate, validation=validation)


def test_proof_rejects_forged_validation_binding():
    original = proof()
    with pytest.raises(ValueError):
        ReleasePlanProof(
            problem=original.problem,
            candidate=original.candidate,
            validation=original.validation.model_copy(update={"candidate_hash": "f" * 64}),
        )


def test_full_evidence_rejects_missing_duplicate_or_cross_version_proofs():
    original = proof()
    recipe_id = original.problem.recipe_instances[0].recipe_id
    kwargs = dict(
        recipe_ids=(recipe_id,),
        knowledge_version=original.problem.knowledge_version,
        rule_version=original.problem.rule_version,
        snapshot_id=original.problem.snapshot_id,
    )
    # 完整competition检查必须100ID；局部契约测试显式传入一个合成ID。
    validate_plan_bindings((original,), **kwargs)
    for records in ((), (original, original)):
        with pytest.raises(ValueError):
            validate_plan_bindings(records, **kwargs)
    with pytest.raises(ValueError):
        validate_plan_bindings((original,), **{**kwargs, "knowledge_version": "wrong"})


def test_recipe_level_review_covers_bound_path_without_relabeling_model_suggestions():
    from app.domain.review import apply_review_patch
    from scripts.verify_all_recipes import review_pending_count
    from tests.unit.test_knowledge_gate import fixture
    from tests.unit.test_review_provenance import approval_patch

    draft, _, _ = fixture()
    reviewed = apply_review_patch(draft, approval_patch(draft))
    assert reviewed.operations[0].review_status != "APPROVED"
    assert review_pending_count((draft,)) == 1
    assert review_pending_count((reviewed,)) == 0
