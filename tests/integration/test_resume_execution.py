"""明确合成的续做规则：保留共享批次的完整实际投入，不恢复原料库存。"""

import time
from datetime import timedelta

import pytest

from app.domain.ports import Deadline
from app.domain.recovery import ResumeProcedure
from app.domain.runtime_session import RecoveryRule
from app.runtime.clock import SimulationClock
from app.runtime.service import RuntimeService
from app.runtime.simulator import Simulator
from app.scheduling.cp_sat import CpSatScheduler
from app.scheduling.greedy import GreedyScheduler
from app.storage.repositories import RuntimeRepository
from app.storage.unit_of_work import UnitOfWork
from app.validation.schedule import ScheduleValidator
from tests.integration.test_failed_execution_retry import shared_failure
from tests.runtime_support import ORIGIN, event


def test_remaining_seconds_and_consumption_alone_do_not_authorize_resume(tmp_path):
    runtime, _, _, _ = shared_failure(tmp_path)
    current = runtime.get("shared-retry")
    failed = current.runtime.executions[0]
    rule = RecoveryRule(
        rule_id="synthetic-unproven-resume",
        task_ids=failed.task_ids,
        kind="RESUME",
        remaining_sec=45,
        evidence_refs=("synthetic:resume-test",),
        knowledge_version=current.runtime.knowledge_version,
        approved=True,
        source_kind="SYNTHETIC",
    )
    with runtime.store.transaction() as tx:
        RuntimeRepository(tx).save(
            current.model_copy(update={"recovery_rules": (rule,)}),
            expected_revision=current.runtime.state_revision,
        )
    current = runtime.get("shared-retry")
    rejected = runtime.apply_event(
        event(
            current,
            "resume",
            "OPERATION_RETRY_REQUESTED",
            {
                "task_id": failed.task_ids[0],
                "execution_id": failed.execution_id,
                "recovery_rule_id": rule.rule_id,
                "consumed": failed.consumed,
                "output_status": "QUALIFIED",
            },
            at=30,
        )
    )
    assert rejected.status == "REJECTED"
    assert runtime.get("shared-retry").runtime.executions == current.runtime.executions


def authorized_resume(runtime):
    current = runtime.get("shared-retry")
    failed = current.runtime.executions[0]
    binding = next(b for b in current.bindings if b.carrier.carrier_id.root == failed.carrier_id)
    rule = RecoveryRule(
        rule_id="synthetic-retained-resume",
        task_ids=failed.task_ids,
        kind="RESUME",
        remaining_sec=45,
        evidence_refs=("synthetic:retained-resume",),
        knowledge_version=current.runtime.knowledge_version,
        approved=True,
        source_kind="SYNTHETIC",
        resume_procedure=ResumeProcedure(
            source_carrier_kind=binding.carrier.kind,
            resource_uses=binding.carrier.resource_uses,
            max_pause_sec=120,
        ),
    )
    with runtime.store.transaction() as tx:
        RuntimeRepository(tx).save(
            current.model_copy(update={"recovery_rules": (rule,)}),
            expected_revision=current.runtime.state_revision,
        )
    return event(
        runtime.get("shared-retry"),
        "resume",
        "OPERATION_RETRY_REQUESTED",
        {
            "task_id": failed.task_ids[0],
            "execution_id": failed.execution_id,
            "recovery_rule_id": rule.rule_id,
            "consumed": failed.consumed,
            "output_status": "QUALIFIED",
        },
        at=30,
    )


@pytest.mark.parametrize("shared", [True, False])
def test_shared_resume_uses_retained_inputs_and_approved_remaining_process(tmp_path, shared):
    runtime, planning, simulator, _ = shared_failure(tmp_path, shared=shared)
    failed = runtime.get("shared-retry").runtime.executions[0]
    request = authorized_resume(runtime)
    result = planning.apply_event(request)
    assert result.status == "PUBLISHED", result
    current = runtime.get("shared-retry")
    retry = next(e for e in current.runtime.executions if e.status == "PENDING")
    binding = next(b for b in current.bindings if set(b.assignment.task_ids) == set(retry.task_ids))
    assert binding.carrier.duration_sec == 45
    assert binding.assignment.interval.start_sec == 30
    assert binding.assignment.interval.end_sec == 75
    assert retry.resumption.retained_inputs == failed.consumed
    assert not retry.consumed
    assert all(
        lot.available.fraction() == 0
        for lot in current.runtime.details.lots
        if lot.lot_id in {m.lot_id.root for m in failed.consumed}
    )
    assert runtime.apply_event(request).status == "APPLIED"
    with runtime.store.engine.connect() as tx:
        problem = RuntimeRepository(tx).problem("shared-retry", result.plan.plan_version)
    for algorithm in (GreedyScheduler(), CpSatScheduler()):
        deadline = Deadline(expires_at_ns=time.monotonic_ns() + 2_000_000_000)
        solved = (
            algorithm.solve(problem, deadline)
            if isinstance(algorithm, GreedyScheduler)
            else algorithm.solve(problem, None, deadline)
        )
        assert solved.candidate is not None, solved
        proof = ScheduleValidator().validate(
            runtime.knowledge, problem.runtime, problem, solved.candidate
        )
        assert proof.valid, proof
    simulator.advance(result.plan.validated.candidate.metrics.makespan_sec)
    after = runtime.get("shared-retry")
    assert after.runtime.executions[0] == failed
    child = next(e for e in after.runtime.executions if e.execution_id == retry.execution_id)
    assert child.status == "COMPLETED"
    assert child.resumption == retry.resumption
    assert not child.consumed
    assert len(
        [
            entry
            for entry in after.ledger
            if entry.kind == "CONSUME" and entry.lot_id in {m.lot_id.root for m in failed.consumed}
        ]
    ) == len(failed.consumed)


def test_resume_cannot_start_after_expiry_even_with_previously_published_plan(tmp_path):
    runtime, planning, _, _ = shared_failure(tmp_path)
    result = planning.apply_event(authorized_resume(runtime))
    assert result.status == "PUBLISHED", result
    before = runtime.get("shared-retry")
    child = next(e for e in before.runtime.executions if e.status == "PENDING")
    rejected = runtime.apply_event(
        event(
            before,
            "late-start",
            "OPERATION_STARTED",
            {
                "task_id": child.task_ids[0],
                "execution_id": child.execution_id,
            },
            at=151,
        )
    )
    assert rejected.status == "REJECTED", rejected
    after = runtime.get("shared-retry")
    assert after.runtime.executions == before.runtime.executions
    assert after.ledger == before.ledger


@pytest.mark.parametrize("tampered", [False, True])
def test_reviewed_resume_binds_exact_source_scope_and_procedure(tmp_path, tampered):
    from app.domain.base import content_hash
    from app.domain.processing_rules import ProcessingRule
    from app.domain.recovery import RecoveryBinding, RecoveryRuleSpec
    from app.services.planning import PlanningService

    runtime, _, simulator, _ = shared_failure(tmp_path)
    request = authorized_resume(runtime)
    current = runtime.get("shared-retry")
    rule = current.recovery_rules[0]
    specification = RecoveryRuleSpec(
        kind="RESUME",
        remaining_sec=rule.remaining_sec,
        resume_procedure=rule.resume_procedure,
        bindings=tuple(
            RecoveryBinding(
                recipe_id=r.recipe_id,
                operation_id=r.operations[0].operation_id,
                recipe_hash=r.semantic_hash(),
                operation_hash=content_hash(r.operations[0]),
            )
            for r in runtime.knowledge.recipes
        ),
    )
    published = ProcessingRule(
        rule_id=rule.rule_id,
        rule_version=current.runtime.rule_version,
        kind="RECOVERY",
        review_status="APPROVED",
        evidence_refs=rule.evidence_refs,
        group_compatibility_predicate=specification.model_dump_json(),
    )
    # 显式合成发布，验证 REVIEWED 内容绑定机制；不写入真实发布包。
    runtime.knowledge = runtime.knowledge.model_copy(
        update={"rules": (*runtime.knowledge.rules, published)}
    )
    reviewed = rule.model_copy(
        update={"source_kind": "REVIEWED", "remaining_sec": 44 if tampered else 45}
    )
    with runtime.store.transaction() as tx:
        RuntimeRepository(tx).save(
            current.model_copy(update={"recovery_rules": (reviewed,)}),
            expected_revision=current.runtime.state_revision,
        )
    planning = PlanningService(runtime, CpSatScheduler())
    result = planning.apply_event(request)
    if tampered:
        assert result.event.status == "REJECTED", result
        assert runtime.get("shared-retry").runtime.executions == current.runtime.executions
    else:
        assert result.status == "PUBLISHED", result
        simulator.advance(result.plan.validated.candidate.metrics.makespan_sec)
        assert all(
            e.status == "COMPLETED" for e in runtime.get("shared-retry").runtime.executions[1:]
        )


@pytest.mark.parametrize(
    "change", ["quantity", "missing_lot", "quality", "expiry", "resources", "waste"]
)
def test_invalid_resume_does_not_change_failed_facts_or_inventory(tmp_path, change):
    runtime, _, _, _ = shared_failure(tmp_path)
    request = authorized_resume(runtime)
    before = runtime.get("shared-retry")
    payload = request.payload
    if change == "quantity":
        movement = payload.consumed[0]
        smaller = movement.model_copy(
            update={"quantity": movement.quantity.model_copy(update={"value": 99})}
        )
        payload = payload.model_copy(update={"consumed": (smaller, *payload.consumed[1:])})
    elif change == "missing_lot":
        payload = payload.model_copy(update={"consumed": payload.consumed[:1]})
    elif change == "quality":
        payload = payload.model_copy(update={"output_status": "UNKNOWN"})
    elif change == "expiry":
        request = request.model_copy(update={"occurred_at": ORIGIN + timedelta(seconds=151)})
    elif change == "resources":
        rule = before.recovery_rules[0]
        modified = rule.model_copy(
            update={
                "resume_procedure": rule.resume_procedure.model_copy(update={"resource_uses": ()})
            }
        )
        before = before.model_copy(update={"recovery_rules": (modified,)})
    else:
        failed = before.runtime.executions[0].model_copy(update={"failure_output_status": "WASTE"})
        before = before.model_copy(
            update={"runtime": before.runtime.model_copy(update={"executions": (failed,)})}
        )
    if change in {"resources", "waste"}:
        with runtime.store.transaction() as tx:
            RuntimeRepository(tx).save(before, expected_revision=before.runtime.state_revision)
    result = runtime.apply_event(request.model_copy(update={"payload": payload}))
    assert result.status == "REJECTED", result
    after = runtime.get("shared-retry")
    assert after.runtime.executions == before.runtime.executions
    assert after.runtime.details.lots == before.runtime.details.lots
    assert after.ledger == before.ledger


def test_resume_cannot_finish_before_authorized_remaining_duration(tmp_path):
    runtime, planning, simulator, _ = shared_failure(tmp_path)
    result = planning.apply_event(authorized_resume(runtime))
    assert result.status == "PUBLISHED", result
    simulator.advance(30)
    current = runtime.get("shared-retry")
    retry = next(e for e in current.runtime.executions if e.status == "RUNNING")
    from app.runtime.simulated_materials import simulated_payload

    with runtime.store.engine.connect() as tx:
        problem = RuntimeRepository(tx).problem("shared-retry", result.plan.plan_version)
    payload = simulated_payload(
        current, problem, retry.task_ids, retry.execution_id.root, completed=True
    )
    rejected = runtime.apply_event(
        event(current, "too-early", "OPERATION_COMPLETED", payload, at=31)
    )
    assert rejected.status == "REJECTED", rejected
    assert runtime.get("shared-retry").runtime.executions == current.runtime.executions


@pytest.mark.parametrize(
    "change",
    [
        "duration",
        "marker",
        "resources",
        "scope",
        "parent_hash",
        "retained",
        "proof_time",
        "planned_time",
    ],
)
def test_independent_validator_rejects_forged_resume(tmp_path, change):
    runtime, planning, _, _ = shared_failure(tmp_path)
    result = planning.apply_event(authorized_resume(runtime))
    assert result.status == "PUBLISHED", result
    with runtime.store.engine.connect() as tx:
        problem = RuntimeRepository(tx).problem("shared-retry", result.plan.plan_version)
    child = next(e for e in problem.runtime.executions if e.status == "PENDING")
    carrier = next(c for c in problem.shared_prep_candidates if c.resume_execution_id)
    candidate = result.plan.validated.candidate
    if change in {"duration", "marker", "resources", "scope"}:
        updates = {
            "duration": {"duration_sec": 44},
            "marker": {"resume_execution_id": None},
            "resources": {"resource_uses": ()},
            "scope": {"covers": carrier.covers[:1]},
        }[change]
        problem = problem.model_copy(
            update={"shared_prep_candidates": (carrier.model_copy(update=updates),)}
        )
    elif change == "planned_time":
        assignments = tuple(
            a.model_copy(
                update={
                    "interval": a.interval.model_copy(update={"start_sec": 151, "end_sec": 196})
                }
            )
            if a.carrier_id == carrier.carrier_id
            else a
            for a in candidate.assignments
        )
        candidate = candidate.model_copy(update={"assignments": assignments})
    else:
        updates = {
            "parent_hash": {"parent_hash": "0" * 64},
            "retained": {"retained_inputs": child.resumption.retained_inputs[:1]},
            "proof_time": {"latest_start_at": ORIGIN + timedelta(seconds=999)},
        }[change]
        modified = child.model_copy(
            update={"resumption": child.resumption.model_copy(update=updates)}
        )
        state = problem.runtime.model_copy(
            update={
                "executions": tuple(
                    modified if e.execution_id == child.execution_id else e
                    for e in problem.runtime.executions
                )
            }
        )
        problem = problem.model_copy(update={"runtime": state})
    candidate = candidate.model_copy(update={"problem_hash": problem.problem_hash})
    proof = ScheduleValidator().validate(runtime.knowledge, problem.runtime, problem, candidate)
    assert not proof.valid
    assert any(v.code.startswith("RECOVERY_") for v in proof.violations), proof


def test_resume_survives_sqlite_restart_and_replans_while_running(tmp_path):
    runtime, planning, simulator, _ = shared_failure(tmp_path)
    result = planning.apply_event(authorized_resume(runtime))
    assert result.status == "PUBLISHED", result
    before = runtime.get("shared-retry")
    reopened = RuntimeService(
        UnitOfWork(tmp_path / "shared-retry.sqlite"), runtime.knowledge, SimulationClock(ORIGIN)
    )
    assert reopened.get("shared-retry") == before
    simulator = Simulator(reopened, "shared-retry", seed=19)
    simulator.advance(30)
    current = reopened.get("shared-retry")
    child = next(e for e in current.runtime.executions if e.status == "RUNNING")
    assert child.resumption and child.resumption.retained_inputs
    from app.services.planning import PlanningService

    planning = PlanningService(reopened, CpSatScheduler())
    updated = planning.apply_event(
        event(
            current,
            "resume-longer",
            "DURATION_UPDATED",
            {
                "task_id": child.task_ids[0],
                "execution_id": child.execution_id,
                "remaining_sec": 50,
            },
            at=35,
        )
    )
    assert updated.status == "PUBLISHED", updated
    simulator.advance(updated.plan.validated.candidate.metrics.makespan_sec)
    after = reopened.get("shared-retry")
    assert all(e.status == "COMPLETED" for e in after.runtime.executions[1:])
    assert after.runtime.executions[0] == before.runtime.executions[0]
