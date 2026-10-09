"""恢复规则为明确标注的合成夹具；真实发布无规则必须拒绝。"""

import pytest

from app.domain.base import content_hash
from app.domain.processing_rules import ProcessingRule
from app.domain.recovery import RecoveryBinding, RecoveryRuleSpec
from app.domain.runtime_session import RecoveryRule
from app.runtime.simulator import Simulator
from app.storage.repositories import RuntimeRepository
from tests.integration.test_material_ledger import material_service
from tests.integration.test_menu_events_replanning import service, start_event
from tests.runtime_support import event, synthetic_knowledge


def failed_attempt(tmp_path, knowledge=None, execution_id="original"):
    runtime, planning, session = service(tmp_path, knowledge=knowledge)
    assert planning.apply_event(start_event(session)).status == "PUBLISHED"
    current = runtime.get("flow")
    binding = min(current.bindings, key=lambda b: b.assignment.interval.start_sec)
    payload = {"task_id": binding.assignment.task_ids[0], "execution_id": execution_id}
    assert (
        runtime.apply_event(event(current, "begin", "OPERATION_STARTED", payload)).status
        == "APPLIED"
    )
    current = runtime.get("flow")
    failed = event(
        current,
        "failed",
        "OPERATION_FAILED",
        {**payload, "reason": "synthetic mixing failure"},
        at=60,
    )
    assert runtime.apply_event(failed).status == "APPLIED"
    assert runtime.apply_event(failed).status == "APPLIED"
    return runtime, planning, payload


def approve_remake(runtime):
    current = runtime.get("flow")
    rule = RecoveryRule(
        rule_id="synthetic-remake",
        task_ids=current.runtime.executions[0].task_ids,
        kind="REMAKE",
        evidence_refs=("synthetic:test_execution_retry",),
        knowledge_version=current.runtime.knowledge_version,
        approved=True,
        source_kind="SYNTHETIC",
    )
    with runtime.store.transaction() as tx:
        RuntimeRepository(tx).save(
            current.model_copy(update={"recovery_rules": (rule,)}),
            expected_revision=current.runtime.state_revision,
        )
    return rule


@pytest.mark.parametrize("defect", ["identity-collision", "before-failure", "duplicate-rule-id"])
def test_retry_preserves_identity_and_causal_order(tmp_path, defect):
    runtime, _, payload = failed_attempt(
        tmp_path, execution_id="retry-approved" if defect == "identity-collision" else "original"
    )
    rule = approve_remake(runtime)
    current = runtime.get("flow")
    if defect == "duplicate-rule-id":
        with runtime.store.transaction() as tx:
            RuntimeRepository(tx).save(
                current.model_copy(update={"recovery_rules": (rule, rule)}),
                expected_revision=current.runtime.state_revision,
            )
        current = runtime.get("flow")
    result = runtime.apply_event(
        event(
            current,
            "approved",
            "OPERATION_RETRY_REQUESTED",
            {**payload, "recovery_rule_id": rule.rule_id},
            at=0 if defect == "before-failure" else 60,
        )
    )
    assert result.status == "REJECTED", result
    assert runtime.get("flow").runtime.executions == current.runtime.executions


def test_no_recovery_rule_keeps_failure_and_blocks_dispatch(tmp_path):
    runtime, planning, payload = failed_attempt(tmp_path)
    rejected = planning.apply_event(
        event(
            runtime.get("flow"),
            "unapproved",
            "OPERATION_RETRY_REQUESTED",
            {**payload, "recovery_rule_id": "missing-rule"},
            at=60,
        )
    )
    assert rejected.status == "EVENT_REJECTED"
    current = runtime.get("flow")
    assert current.dispatch_blocked
    assert len(current.runtime.executions) == 1
    assert current.runtime.executions[0].status == "FAILED"


@pytest.mark.parametrize(
    "defect",
    [
        "missing-published-rule",
        "unmarked-synthetic",
        "duplicate-members",
        "remake-with-resume-duration",
    ],
)
def test_retry_rejects_unverifiable_authorization(tmp_path, defect):
    runtime, _, payload = failed_attempt(tmp_path)
    rule = approve_remake(runtime)
    changes = {
        "missing-published-rule": {"source_kind": "REVIEWED"},
        "unmarked-synthetic": {"evidence_refs": ("untraceable-approval",)},
        "duplicate-members": {"task_ids": (*rule.task_ids, *rule.task_ids)},
        "remake-with-resume-duration": {"remaining_sec": 10},
    }[defect]
    current = runtime.get("flow")
    with runtime.store.transaction() as tx:
        RuntimeRepository(tx).save(
            current.model_copy(update={"recovery_rules": (rule.model_copy(update=changes),)}),
            expected_revision=current.runtime.state_revision,
        )
    current = runtime.get("flow")
    result = runtime.apply_event(
        event(
            current,
            "forged-retry",
            "OPERATION_RETRY_REQUESTED",
            {**payload, "recovery_rule_id": rule.rule_id},
            at=60,
        )
    )
    assert result.status == "REJECTED", result
    assert runtime.get("flow").runtime.executions == current.runtime.executions


@pytest.mark.parametrize(
    "defect",
    [
        None,
        "recipe-hash",
        "operation-hash",
        "version",
        "wrong-kind",
        "unapproved",
        "missing-member",
    ],
)
def test_published_recovery_binds_exact_recipe_operation_and_version(tmp_path, defect):
    # 审核记录为明确合成的发布夹具，在会话创建前装配；不改写真实知识包。
    knowledge = synthetic_knowledge()
    recipe = knowledge.recipes[0]
    operation = recipe.operations[0]
    binding = RecoveryBinding(
        recipe_id=recipe.recipe_id,
        operation_id=operation.operation_id,
        recipe_hash="0" * 64 if defect == "recipe-hash" else recipe.semantic_hash(),
        operation_hash="0" * 64 if defect == "operation-hash" else content_hash(operation),
    )
    if defect == "missing-member":
        binding = binding.model_copy(update={"operation_id": recipe.operations[1].operation_id})
    specification = RecoveryRuleSpec(bindings=(binding,), kind="REMAKE")
    published = ProcessingRule(
        rule_id="synthetic-remake",
        rule_version="wrong-version" if defect == "version" else knowledge.release.rule_version,
        kind="TRANSITION" if defect == "wrong-kind" else "RECOVERY",
        group_compatibility_predicate=specification.model_dump_json(),
        evidence_refs=("synthetic:test_execution_retry",),
        review_status="DRAFT" if defect == "unapproved" else "APPROVED",
    )
    knowledge = knowledge.model_copy(update={"rules": (published,)})
    runtime, planning, payload = failed_attempt(tmp_path, knowledge)
    rule = approve_remake(runtime)
    current = runtime.get("flow")
    with runtime.store.transaction() as tx:
        RuntimeRepository(tx).save(
            current.model_copy(
                update={"recovery_rules": (rule.model_copy(update={"source_kind": "REVIEWED"}),)}
            ),
            expected_revision=current.runtime.state_revision,
        )
    current = runtime.get("flow")
    result = planning.apply_event(
        event(
            current,
            "published-retry",
            "OPERATION_RETRY_REQUESTED",
            {**payload, "recovery_rule_id": rule.rule_id},
            at=60,
        )
    )
    assert result.status == ("PUBLISHED" if defect is None else "EVENT_REJECTED"), result
    if defect is not None:
        assert runtime.get("flow").runtime.executions == current.runtime.executions


def test_approved_remake_uses_new_identity_and_keeps_failed_human_history(tmp_path):
    runtime, planning, payload = failed_attempt(tmp_path)
    rule = approve_remake(runtime)
    retried = planning.apply_event(
        event(
            runtime.get("flow"),
            "approved",
            "OPERATION_RETRY_REQUESTED",
            {**payload, "recovery_rule_id": rule.rule_id},
            at=60,
        )
    )
    assert retried.status == "PUBLISHED", retried
    current = runtime.get("flow")
    pending = next(e for e in current.runtime.executions if e.status == "PENDING")
    assert pending.previous_execution_id.root == "original"
    assert pending.execution_id.root == "retry-approved"
    assert retried.plan.validated.candidate.metrics.actual_human_work_sec == 60
    started = runtime.apply_event(
        event(
            current,
            "retry-start",
            "OPERATION_STARTED",
            {**payload, "execution_id": pending.execution_id},
            at=60,
        )
    )
    assert started.status == "APPLIED", started
    current = runtime.get("flow")
    completed = planning.apply_event(
        event(
            current,
            "retry-end",
            "OPERATION_COMPLETED",
            {**payload, "execution_id": pending.execution_id},
            at=240,
        )
    )
    assert completed.status == "NO_REPLAN", completed
    records = runtime.get("flow").runtime.executions
    assert [r.status for r in records] == ["FAILED", "COMPLETED"]
    assert records[1].previous_execution_id == records[0].execution_id
    assert records[1].event_refs[0].root == "approved"
    assert records[0].finished_at == current.runtime.time_origin.at(60)


def test_pending_retry_cannot_be_bypassed_with_an_arbitrary_execution_id(tmp_path):
    runtime, planning, payload = failed_attempt(tmp_path)
    rule = approve_remake(runtime)
    assert (
        planning.apply_event(
            event(
                runtime.get("flow"),
                "approved",
                "OPERATION_RETRY_REQUESTED",
                {**payload, "recovery_rule_id": rule.rule_id},
                at=60,
            )
        ).status
        == "PUBLISHED"
    )
    result = runtime.apply_event(
        event(
            runtime.get("flow"),
            "bypass",
            "OPERATION_STARTED",
            {**payload, "execution_id": "unapproved-id"},
            at=60,
        )
    )
    assert result.status != "APPLIED"
    assert all(
        r.execution_id.root != "unapproved-id" for r in runtime.get("flow").runtime.executions
    )


@pytest.mark.parametrize("failed_quality", ["WASTE", "QUALIFIED"])
def test_remake_preserves_failed_output_without_implicit_salvage(tmp_path, failed_quality):
    runtime, planning = material_service(tmp_path)
    simulator = Simulator(runtime, "material", seed=17)
    simulator.advance(0)
    current = runtime.get("material")
    failed = current.runtime.executions[0]
    binding = next(b for b in current.bindings if failed.task_ids[0] in b.assignment.task_ids)
    raw = current.runtime.details.lots[0]
    lost = {
        "lot_id": "synthetic-waste",
        "spec_id": binding.carrier.material_outputs[0].spec_id,
        "quantity": {"value": 3, "unit": "g", "scale": 1},
    }
    failure = event(
        current,
        "material-failure",
        "OPERATION_FAILED",
        {
            "task_id": failed.task_ids[0],
            "execution_id": failed.execution_id,
            "consumed": failed.consumed,
            "produced": [lost],
            "output_status": failed_quality,
            "reason": "synthetic failed processing",
        },
        at=30,
    )
    assert runtime.apply_event(failure).status == "APPLIED"
    current = runtime.get("material")
    rule = RecoveryRule(
        rule_id="synthetic-material-remake",
        task_ids=failed.task_ids,
        kind="REMAKE",
        evidence_refs=("synthetic:replacement raw stock",),
        knowledge_version=current.runtime.knowledge_version,
        approved=True,
        source_kind="SYNTHETIC",
    )
    with runtime.store.transaction() as tx:
        RuntimeRepository(tx).save(
            current.model_copy(update={"recovery_rules": (rule,)}),
            expected_revision=current.runtime.state_revision,
        )
    current = runtime.get("material")
    replenished = event(
        current,
        "replenish",
        "MATERIAL_ADJUSTED",
        {
            "lot_id": raw.lot_id,
            "before": {"value": 0, "unit": "g", "scale": 1},
            "after": {"value": 10, "unit": "g", "scale": 1},
            "reason": "synthetic replacement delivery",
            "evidence_refs": ["synthetic:replacement received"],
        },
        at=60,
    )
    assert runtime.apply_event(replenished).status == "APPLIED"
    outcome = planning.apply_event(
        event(
            runtime.get("material"),
            "material-retry",
            "OPERATION_RETRY_REQUESTED",
            {
                "task_id": failed.task_ids[0],
                "execution_id": failed.execution_id,
                "recovery_rule_id": rule.rule_id,
            },
            at=60,
        )
    )
    assert outcome.status == "PUBLISHED", outcome
    simulator.advance(outcome.plan.validated.candidate.metrics.makespan_sec)
    after = runtime.get("material")
    assert [e.status for e in after.runtime.executions] == ["FAILED", "COMPLETED", "COMPLETED"]
    waste = next(lot for lot in after.runtime.details.lots if lot.lot_id == "synthetic-waste")
    assert waste.produced.fraction() == 3
    assert waste.available.fraction() == (3 if failed_quality == "QUALIFIED" else 0)
    assert not any(a.lot_id == waste.lot_id for a in after.allocations)
    assert len([entry for entry in after.ledger if entry.kind == "LOSS"]) == (
        0 if failed_quality == "QUALIFIED" else 1
    )
    assert (
        len(
            [
                entry
                for entry in after.ledger
                if entry.kind == "CONSUME" and entry.lot_id == raw.lot_id
            ]
        )
        == 2
    )
    assert after.runtime.executions[1].previous_execution_id == failed.execution_id
