"""合成共享工艺：失败保留两份消费，审核重做保留完整成员并重新投入。"""

import time

from app.domain.base import content_hash
from app.domain.compatibility import GroupBinding, GroupRuleSpec
from app.domain.ports import Deadline
from app.domain.processing_rules import ProcessingRule
from app.domain.runtime_session import RecoveryRule
from app.domain.schedule import ScheduledAssignment
from app.domain.time import Interval
from app.runtime.clock import SimulationClock
from app.runtime.service import RuntimeService
from app.runtime.simulator import Simulator
from app.scheduling.cp_sat import CpSatScheduler
from app.scheduling.greedy import GreedyScheduler
from app.services.planning import PlanningService
from app.storage.repositories import RuntimeRepository
from app.storage.unit_of_work import UnitOfWork
from app.validation.schedule import ScheduleValidator
from tests.integration.test_inventory_substitution import synthetic_recipe
from tests.runtime_support import ORIGIN, event, p4_knowledge, policy


def shared_runtime(tmp_path, *, shared=True, extra_recipes=()):
    recipes = tuple(
        synthetic_recipe(f"synthetic-shared-retry-{i}", 100, source=True) for i in range(2)
    )
    base = p4_knowledge()
    specification = GroupRuleSpec(
        bindings=tuple(
            GroupBinding(
                recipe_id=recipe.recipe_id,
                operation_id=recipe.operations[0].operation_id,
                recipe_hash=recipe.semantic_hash(),
                operation_hash=content_hash(recipe.operations[0]),
                processing_spec="synthetic:cut-10mm",
                configuration_keys=("synthetic:human-cut",),
            )
            for recipe in recipes
        ),
        duration_sec=90,
        authority="DELEGATED_DEVELOPMENT_ESTIMATE",
        authorization_ref="synthetic:shared-retry-test",
        scope_note="明确合成，两份独立原料共同切配",
    )
    rule = ProcessingRule(
        rule_id="synthetic-shared-retry",
        rule_version=base.release.rule_version,
        kind="SHARED_PREP",
        group_compatibility_predicate=specification.model_dump_json(),
        evidence_refs=("synthetic:shared-retry-test",),
        review_status="NEEDS_REVIEW",
    )
    knowledge = base.model_copy(
        update={"recipes": (*recipes, *extra_recipes), "recipe_contexts": (), "rules": (rule,)}
    )
    store = UnitOfWork(tmp_path / "shared-retry.sqlite")
    store.migrate()
    runtime = RuntimeService(store, knowledge, SimulationClock(ORIGIN))
    session = runtime.create_session(
        "shared-retry", "SIMULATED", ORIGIN, policy().model_copy(update={"shared_prep": shared})
    )
    planning = PlanningService(runtime, CpSatScheduler())
    result = planning.apply_event(
        event(
            session,
            "shared-start",
            "START_SESSION",
            {"recipes": [{"id": recipe.recipe_id, "name": recipe.name} for recipe in recipes]},
        )
    )
    assert result.status == "PUBLISHED", result
    assert (
        any(item.carrier.kind == "SHARED_PREP" for item in runtime.get("shared-retry").bindings)
        == shared
    )
    simulator = Simulator(runtime, "shared-retry", seed=19)
    simulator.advance(0)
    current = runtime.get("shared-retry")
    record = next(item for item in current.runtime.executions if item.status == "RUNNING")
    return runtime, planning, simulator, record


def shared_failure(tmp_path, *, shared=True):
    runtime, planning, simulator, record = shared_runtime(tmp_path, shared=shared)
    current = runtime.get("shared-retry")
    failure = event(
        current,
        "shared-failed",
        "OPERATION_FAILED",
        {
            "task_id": record.task_ids[0],
            "execution_id": record.execution_id,
            "consumed": record.consumed,
            "reason": "synthetic:shared batch failure",
        },
        at=30,
    )
    assert runtime.apply_event(failure).status == "APPLIED"
    assert runtime.apply_event(failure).status == "APPLIED"
    return runtime, planning, simulator, record


def test_shared_failure_remake_keeps_members_and_never_restores_consumed_raw(tmp_path):
    runtime, planning, simulator, original = shared_failure(tmp_path)
    current = runtime.get("shared-retry")
    failed = current.runtime.executions[0]
    assert failed.status == "FAILED" and len(failed.consumed) == 2
    assert len([item for item in current.ledger if item.kind == "CONSUME"]) == 2
    assert all(item.available.fraction() == 0 for item in current.runtime.details.lots)
    rule = RecoveryRule(
        rule_id="synthetic-full-group-remake",
        task_ids=failed.task_ids,
        kind="REMAKE",
        evidence_refs=("synthetic:full-group-remake",),
        knowledge_version=current.runtime.knowledge_version,
        approved=True,
        source_kind="SYNTHETIC",
    )
    with runtime.store.transaction() as tx:
        RuntimeRepository(tx).save(
            current.model_copy(update={"recovery_rules": (rule,)}),
            expected_revision=current.runtime.state_revision,
        )
    for movement in original.consumed:
        current = runtime.get("shared-retry")
        result = runtime.apply_event(
            event(
                current,
                "refill-" + movement.lot_id.root,
                "MATERIAL_ADJUSTED",
                {
                    "lot_id": movement.lot_id,
                    "before": {"value": 0, "unit": "g", "scale": 1},
                    "after": {"value": 100, "unit": "g", "scale": 1},
                    "reason": "synthetic:replacement material",
                    "evidence_refs": ["synthetic:received"],
                },
                at=30,
            )
        )
        assert result.status == "APPLIED", result
    current = runtime.get("shared-retry")
    result = planning.apply_event(
        event(
            current,
            "shared-approved",
            "OPERATION_RETRY_REQUESTED",
            {
                "task_id": failed.task_ids[0],
                "execution_id": failed.execution_id,
                "recovery_rule_id": rule.rule_id,
            },
            at=30,
        )
    )
    assert result.status == "PUBLISHED", result
    current = runtime.get("shared-retry")
    retry = next(item for item in current.runtime.executions if item.status == "PENDING")
    binding = next(
        item
        for item in current.bindings
        if item.plan_version == result.plan.plan_version
        and set(item.assignment.task_ids) == set(retry.task_ids)
    )
    assert binding.carrier.kind == "SHARED_PREP"
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
        assert any(
            set(item.task_ids) == set(retry.task_ids) for item in solved.candidate.assignments
        )
    # 绕过 Compiler 塞回单独载体时，独立校验仍须拒绝拆分重试成员。
    with runtime.store.engine.connect() as tx:
        original_problem = RuntimeRepository(tx).problem("shared-retry", 1)
    singles = tuple(
        item
        for item in original_problem.standalone_candidates
        if set(item.covers) <= set(retry.task_ids)
    )
    forged_problem = problem.model_copy(
        update={"standalone_candidates": (*problem.standalone_candidates, *singles)}
    )
    split = tuple(
        ScheduledAssignment(
            carrier_id=item.carrier_id,
            task_ids=item.covers,
            interval=Interval(start_sec=30 + index * 60, end_sec=90 + index * 60),
            resource_uses=item.resource_uses,
        )
        for index, item in enumerate(singles)
    )
    forged_candidate = result.plan.validated.candidate.model_copy(
        update={
            "problem_hash": forged_problem.problem_hash,
            "assignments": (
                *[
                    item
                    for item in result.plan.validated.candidate.assignments
                    if not set(item.task_ids).intersection(retry.task_ids)
                ],
                *split,
            ),
        }
    )
    rejection = ScheduleValidator().validate(
        runtime.knowledge, forged_problem.runtime, forged_problem, forged_candidate
    )
    assert any(item.code == "RECOVERY_SCOPE" for item in rejection.violations)
    simulator.advance(result.plan.validated.candidate.metrics.makespan_sec)
    after = runtime.get("shared-retry")
    assert after.runtime.executions[0] == failed
    assert len(after.runtime.executions) == 4
    assert all(item.status == "COMPLETED" for item in after.runtime.executions[1:])
    assert after.runtime.executions[1].previous_execution_id == failed.execution_id
    assert set(after.runtime.executions[1].task_ids) == set(failed.task_ids)
    assert (
        len(
            [
                item
                for item in after.ledger
                if item.kind == "CONSUME"
                and item.lot_id in {m.lot_id.root for m in failed.consumed}
            ]
        )
        == 4
    )


def test_shared_retry_rule_cannot_omit_an_affected_member(tmp_path):
    runtime, _, _, _ = shared_failure(tmp_path)
    current = runtime.get("shared-retry")
    failed = current.runtime.executions[0]
    rule = RecoveryRule(
        rule_id="synthetic-partial-remake",
        task_ids=failed.task_ids[:1],
        kind="REMAKE",
        evidence_refs=("synthetic:partial-scope",),
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
            "partial-retry",
            "OPERATION_RETRY_REQUESTED",
            {
                "task_id": failed.task_ids[0],
                "execution_id": failed.execution_id,
                "recovery_rule_id": rule.rule_id,
            },
            at=30,
        )
    )
    assert rejected.status == "REJECTED", rejected
    assert runtime.get("shared-retry").runtime.executions == current.runtime.executions
