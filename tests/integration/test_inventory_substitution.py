"""显式合成规格与规则：真实余料 80g 供应新菜 50g，完整替代两步前处理。"""

import time
from copy import deepcopy

import pytest
from sqlalchemy import select

from app.domain.candidates import stable_id
from app.domain.canonical_recipe import CanonicalRecipeModel
from app.domain.ports import Deadline
from app.domain.runtime_facts import RationalAmount
from app.domain.runtime_session import InventoryRule
from app.runtime.clock import SimulationClock
from app.runtime.service import RuntimeService
from app.runtime.simulator import Simulator
from app.scheduling.cp_sat import CpSatScheduler
from app.scheduling.greedy import GreedyScheduler
from app.services.planning import PlanningService
from app.storage import models
from app.storage.repositories import RuntimeRepository
from app.storage.unit_of_work import UnitOfWork
from app.validation.schedule import ScheduleValidator
from tests.runtime_support import ORIGIN, event, p4_knowledge, policy


def synthetic_recipe(identity, amount, *, source=False):
    def requirement(identity, spec, amount):
        return {
            "requirement_id": identity,
            "spec_id": spec,
            "quantity_kind": "EXACT",
            "quantity": {"value": amount, "unit": "g", "scale": 1},
            "provenance_refs": ["synthetic:inventory"],
        }

    specs = [
        {
            "spec_id": name,
            "ingredient_id": "synthetic-carrot",
            "name": name,
            "state": name,
            "shape": "丁" if name == "cut" else None,
            "size_mm": 10 if name == "cut" else None,
            "treatment": "清洗后切配" if name == "cut" else None,
        }
        for name in ("raw", "washed", "cut", "final")
    ]
    steps = (
        [("source-cut", "raw", "cut", amount), ("source-finish", "cut", "final", 20)]
        if source
        else [
            ("wash", "raw", "washed", amount),
            ("cut", "washed", "cut", amount),
            ("finish", "cut", "final", amount),
        ]
    )
    operations = [
        {
            "operation_id": name,
            "action": "CUT",
            "duration": {"execution_sec": 60},
            "material_inputs": [requirement(name + "-in", before, quantity)],
            "material_outputs": [requirement(name + "-out", after, quantity)],
            "resource_requirements": [
                {"resource_type": "HUMAN", "resource_id": "human_1", "conflict_policy": "UNARY"}
            ],
        }
        for name, before, after, quantity in steps
    ]
    return CanonicalRecipeModel.model_validate(
        {
            "schema_version": "1.0",
            "recipe_id": identity,
            "name": identity,
            "recipe_version": "1",
            "provenance_refs": ["synthetic:inventory"],
            "material_specs": specs,
            "ingredient_requirements": [requirement("raw", "raw", amount)],
            "operations": operations,
            "dependencies": [
                {
                    "predecessor_id": before[0],
                    "successor_id": after[0],
                    "reason": "synthetic material flow",
                    "evidence_refs": ["synthetic:inventory"],
                }
                for before, after in zip(steps, steps[1:], strict=False)
            ],
        }
    )


def stock_service(tmp_path, *, add_count=1, with_rule=True):
    source = synthetic_recipe("synthetic-stock-source", 100, source=True)
    target = synthetic_recipe("synthetic-stock-target", 50)
    knowledge = p4_knowledge().model_copy(
        update={"recipes": (source, target), "recipe_contexts": (), "rules": ()}
    )
    store = UnitOfWork(tmp_path / "inventory.sqlite")
    store.migrate()
    runtime = RuntimeService(store, knowledge, SimulationClock(ORIGIN))
    session = runtime.create_session("inventory", "SIMULATED", ORIGIN, policy())
    planning = PlanningService(runtime, CpSatScheduler())
    initial = planning.apply_event(
        event(
            session,
            "source-start",
            "START_SESSION",
            {"recipes": [{"id": source.recipe_id, "name": source.name}]},
        )
    )
    assert initial.status == "PUBLISHED", initial
    simulator = Simulator(runtime, "inventory", seed=47)
    simulator.advance(initial.plan.validated.candidate.metrics.makespan_sec)
    current = runtime.get("inventory")
    lot = next(lot for lot in current.runtime.details.lots if lot.source_spec_id == "cut")
    assert lot.available.fraction() == 80
    assert (
        runtime.apply_event(
            event(
                current,
                "add-targets",
                "ADD_RECIPE",
                {"recipes": [{"id": target.recipe_id, "name": target.name}] * add_count},
                at=120,
            )
        ).status
        == "APPLIED"
    )
    current = runtime.get("inventory")
    rules = tuple(
        InventoryRule(
            rule_id=f"synthetic-inventory-{index}",
            target_task_ids=tuple(
                stable_id("task", instance.recipe_instance_id.root, operation)
                for operation in ("wash", "cut")
            ),
            source_spec_id=lot.spec_id,
            quantity=RationalAmount(numerator=50),
            unit="g",
            evidence_refs=("synthetic:complete-preparation-substitution",),
            knowledge_version=current.runtime.knowledge_version,
            source_kind="SYNTHETIC",
            approved=True,
            max_age_sec=3600,
        )
        for index, instance in enumerate(current.menu[1:])
    )
    if with_rule:
        # 明确合成的规则夹具，不改写正式知识发布和审核证据。
        with store.transaction() as tx:
            RuntimeRepository(tx).save(
                current.model_copy(update={"inventory_rules": rules}),
                expected_revision=current.runtime.state_revision,
            )
    return runtime, planning, simulator, lot, rules


def inventory_bindings(runtime):
    return [
        binding
        for binding in runtime.get("inventory").bindings
        if binding.carrier.kind == "INVENTORY_SUPPLY"
    ]


def test_inventory_covers_full_preparation_without_fabricating_execution(tmp_path):
    runtime, planning, simulator, lot, rules = stock_service(tmp_path)
    before = runtime.get("inventory")
    published = planning.drain("inventory")
    assert published.status == "PUBLISHED", published
    selected = inventory_bindings(runtime)
    assert len(selected) == 1
    assert set(selected[0].assignment.task_ids) == set(rules[0].target_task_ids)
    assert selected[0].assignment.interval.start_sec == selected[0].assignment.interval.end_sec
    assert published.plan.validated.candidate.metrics.makespan_sec == 180
    assert runtime.get("inventory").runtime.executions == before.runtime.executions
    simulator.advance(120)
    current = runtime.get("inventory")
    assert not any(
        set(record.task_ids) & set(rules[0].target_task_ids)
        for record in current.runtime.executions
    )
    assert len(current.runtime.executions) == 3
    assert (
        next(
            item for item in current.runtime.details.lots if item.lot_id == lot.lot_id
        ).available.fraction()
        == 30
    )
    assert current.runtime.details.inventory_fulfillments[0].status == "COMMITTED"
    # 消费开始后再重排，已满足的前处理保持供应身份，不变成一次虚构切配。
    changed = planning.apply_event(
        event(
            current,
            "after-inventory-start",
            "DELAY_RECIPE",
            {"recipe_instance_id": current.menu[-1].recipe_instance_id, "earliest_start_sec": 120},
            at=120,
        )
    )
    assert changed.status == "PUBLISHED", changed
    simulator.advance(changed.plan.validated.candidate.metrics.makespan_sec)
    completed = runtime.get("inventory")
    assert len(completed.runtime.executions) == 3
    assert all(record.status == "COMPLETED" for record in completed.runtime.executions)
    assert completed.runtime.executions[:2] == before.runtime.executions
    assert not any(
        set(record.task_ids) & set(rules[0].target_task_ids)
        for record in completed.runtime.executions
    )


def test_no_rule_keeps_all_preparation_tasks(tmp_path):
    runtime, planning, _, _, _ = stock_service(tmp_path, with_rule=False)
    outcome = planning.drain("inventory")
    assert outcome.status == "PUBLISHED", outcome
    assert not inventory_bindings(runtime)
    assert outcome.plan.validated.candidate.metrics.makespan_sec == 300


@pytest.mark.parametrize(
    "defect",
    [
        "incomplete-scope",
        "wrong-quantity",
        "missing-evidence",
        "wrong-version",
        "unknown-quality",
        "future-lot",
        "expired-lot",
        "missing-validity",
    ],
)
def test_invalid_inventory_never_skips_preparation(tmp_path, defect):
    runtime, planning, _, lot, rules = stock_service(tmp_path)
    current = runtime.get("inventory")
    rule, details = rules[0], current.runtime.details
    if defect == "incomplete-scope":
        rule = rule.model_copy(update={"target_task_ids": rule.target_task_ids[1:]})
    elif defect == "wrong-quantity":
        rule = rule.model_copy(update={"quantity": RationalAmount(numerator=49)})
    elif defect == "missing-evidence":
        rule = rule.model_copy(update={"evidence_refs": ()})
    elif defect == "wrong-version":
        rule = rule.model_copy(update={"knowledge_version": "unrelated"})
    elif defect == "missing-validity":
        rule = rule.model_copy(update={"max_age_sec": None})
    else:
        updates = (
            {"quality_status": "UNKNOWN"}
            if defect == "unknown-quality"
            else {"produced_at": current.runtime.time_origin.at(121)}
            if defect == "future-lot"
            else {"expires_at": current.runtime.time_origin.at(119)}
        )
        details = details.model_copy(
            update={
                "lots": tuple(
                    item.model_copy(update=deepcopy(updates)) if item.lot_id == lot.lot_id else item
                    for item in details.lots
                )
            }
        )
    with runtime.store.transaction() as tx:
        RuntimeRepository(tx).save(
            current.model_copy(
                update={
                    "inventory_rules": (rule,),
                    "runtime": current.runtime.model_copy(update={"details": details}),
                }
            ),
            expected_revision=current.runtime.state_revision,
        )
    outcome = planning.drain("inventory")
    assert outcome.status in {"PUBLISHED", "FAILED"}
    assert not inventory_bindings(runtime)
    if outcome.status == "PUBLISHED":
        assert outcome.plan.validated.candidate.metrics.makespan_sec == 300


def test_two_new_recipes_cannot_both_spend_the_same_eighty_grams(tmp_path):
    runtime, planning, simulator, lot, _ = stock_service(tmp_path, add_count=2)
    outcome = planning.drain("inventory")
    assert outcome.status == "PUBLISHED", outcome
    assert len(inventory_bindings(runtime)) == 1
    simulator.advance(outcome.plan.validated.candidate.metrics.makespan_sec)
    after = runtime.get("inventory")
    assert (
        next(
            item for item in after.runtime.details.lots if item.lot_id == lot.lot_id
        ).available.fraction()
        == 30
    )
    assert len(after.runtime.executions) == 6  # 旧菜两步、一份库存后的收尾、另一份完整三步。


@pytest.mark.parametrize("algorithm", ["greedy", "cp_sat"])
def test_each_algorithm_respects_shared_stock_capacity(tmp_path, algorithm):
    runtime, planning, _, _, _ = stock_service(tmp_path, add_count=2)
    published = planning.drain("inventory")
    assert published.status == "PUBLISHED", published
    with runtime.store.engine.connect() as tx:
        problem = RuntimeRepository(tx).problem("inventory", published.plan.plan_version)
    deadline = Deadline(expires_at_ns=time.monotonic_ns() + 2_000_000_000)
    result = (
        GreedyScheduler().solve(problem, deadline)
        if algorithm == "greedy"
        else CpSatScheduler().solve(problem, None, deadline)
    )
    assert result.candidate is not None, result
    report = ScheduleValidator().validate(
        runtime.knowledge, problem.runtime, problem, result.candidate
    )
    assert report.valid, report
    ids = {carrier.carrier_id for carrier in problem.inventory_supply_candidates}
    assert sum(item.carrier_id in ids for item in result.candidate.assignments) == 1


def test_inventory_publication_survives_restart_without_fake_notifications(tmp_path):
    runtime, planning, _, _, rules = stock_service(tmp_path)
    result = planning.drain("inventory")
    assert result.status == "PUBLISHED", result
    current = runtime.get("inventory")
    record = current.runtime.details.inventory_fulfillments[0]
    with runtime.store.engine.connect() as tx:
        assert (
            tx.execute(select(models.inventory_fulfillments.c.body)).scalar_one()
            == record.model_dump_json()
        )
        notices = (
            tx.execute(
                select(models.notifications.c.body).where(
                    models.notifications.c.plan_version == result.plan.plan_version
                )
            )
            .scalars()
            .all()
        )
    assert not any(task.root in body for task in rules[0].target_task_ids for body in notices)
    path = runtime.store.path
    runtime.store.close()
    reopened = UnitOfWork(path)
    reopened.migrate()
    recovered = RuntimeService(reopened, runtime.knowledge, SimulationClock(ORIGIN))
    assert recovered.get("inventory") == current
    reopened.close()


@pytest.mark.parametrize("defect", ["spec", "amount", "unit", "proof", "scope"])
def test_validator_rejects_forged_inventory_without_compiler_help(tmp_path, defect):
    runtime, planning, _, _, _ = stock_service(tmp_path)
    result = planning.drain("inventory")
    assert result.status == "PUBLISHED", result
    with runtime.store.engine.connect() as tx:
        problem = RuntimeRepository(tx).problem("inventory", result.plan.plan_version)
    carrier = problem.inventory_supply_candidates[0]
    supply = carrier.inventory_supply
    if defect == "spec":
        supply = supply.model_copy(
            update={"target_spec": supply.target_spec.model_copy(update={"size_mm": 99})}
        )
    elif defect in {"amount", "unit"}:
        claim = supply.lot_claims[0]
        amount = claim.quantity.model_copy(
            update={"value": 49} if defect == "amount" else {"unit": "ml"}
        )
        supply = supply.model_copy(
            update={"lot_claims": (claim.model_copy(update={"quantity": amount}),)}
        )
    elif defect == "proof":
        supply = supply.model_copy(update={"rule_id": "missing-rule"})
    else:
        carrier = carrier.model_copy(update={"covers": carrier.covers[1:]})
    carrier = carrier.model_copy(update={"inventory_supply": supply})
    forged = problem.model_copy(update={"inventory_supply_candidates": (carrier,)})
    candidate = result.plan.validated.candidate.model_copy(
        update={"problem_hash": forged.problem_hash}
    )
    report = ScheduleValidator().validate(runtime.knowledge, forged.runtime, forged, candidate)
    assert not report.valid


def test_inventory_late_start_rejected_but_on_time_consumption_can_finish_later(tmp_path):
    runtime, planning, simulator, lot, rules = stock_service(tmp_path)
    current = runtime.get("inventory")
    with runtime.store.transaction() as tx:
        RuntimeRepository(tx).save(
            current.model_copy(
                update={"inventory_rules": (rules[0].model_copy(update={"max_age_sec": 61}),)}
            ),
            expected_revision=current.runtime.state_revision,
        )
    result = planning.drain("inventory")
    assert result.status == "PUBLISHED", result
    current = runtime.get("inventory")
    fulfillment = current.runtime.details.inventory_fulfillments[0]
    assert fulfillment.supply.expires_at_sec == 121
    binding = next(
        item
        for item in current.bindings
        if item.plan_version == result.plan.plan_version and item.carrier.kind == "STANDALONE"
    )
    rejected = runtime.apply_event(
        event(
            current,
            "expired-consume",
            "OPERATION_STARTED",
            {
                "task_id": binding.assignment.task_ids[0],
                "execution_id": "expired-attempt",
                "consumed": [
                    {
                        "lot_id": lot.lot_id,
                        "spec_id": lot.spec_id,
                        "quantity": {"value": 50, "unit": "g", "scale": 1},
                    }
                ],
            },
            at=121,
        )
    )
    assert rejected.status == "REJECTED"
    assert runtime.get("inventory").runtime.executions == current.runtime.executions
    simulator.advance(180)
    after = runtime.get("inventory")
    assert len(after.runtime.executions) == 3
    assert all(item.status == "COMPLETED" for item in after.runtime.executions)


def test_replan_supersedes_unstarted_inventory_without_doubling_reservations(tmp_path):
    runtime, planning, _, lot, _ = stock_service(tmp_path)
    first = planning.drain("inventory")
    assert first.status == "PUBLISHED", first
    current = runtime.get("inventory")
    after = planning.apply_event(
        event(
            current,
            "change-window",
            "DELAY_RECIPE",
            {"recipe_instance_id": current.menu[-1].recipe_instance_id, "earliest_start_sec": 180},
            at=120,
        )
    )
    assert after.status == "PUBLISHED", after
    current = runtime.get("inventory")
    assert [item.status for item in current.runtime.details.inventory_fulfillments] == [
        "SUPERSEDED",
        "PLANNED",
    ]
    stock = next(item for item in current.runtime.details.lots if item.lot_id == lot.lot_id)
    assert stock.available.fraction() == 80
    assert stock.reserved.fraction() == 50
