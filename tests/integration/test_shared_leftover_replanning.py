"""合成审核范围：共享实际产出余料供给新增菜，保留原共享执行历史。"""

import time

from app.domain.candidates import stable_id
from app.domain.ports import Deadline
from app.domain.runtime_facts import RationalAmount
from app.domain.runtime_session import InventoryRule
from app.scheduling.cp_sat import CpSatScheduler
from app.scheduling.greedy import GreedyScheduler
from app.storage.repositories import RuntimeRepository
from app.validation.schedule import ScheduleValidator
from tests.integration.test_failed_execution_retry import shared_runtime
from tests.integration.test_inventory_substitution import synthetic_recipe
from tests.runtime_support import event


def test_actual_shared_leftover_supplies_new_recipe_without_repeating_preparation(tmp_path):
    target = synthetic_recipe("synthetic-shared-stock-target", 50)
    runtime, planning, simulator, shared = shared_runtime(tmp_path, extra_recipes=(target,))
    with runtime.store.engine.connect() as tx:
        initial = RuntimeRepository(tx).plan("shared-retry", 1)
    simulator.advance(initial.validated.candidate.metrics.makespan_sec)
    current = runtime.get("shared-retry")
    original = next(
        item for item in current.runtime.executions if item.execution_id == shared.execution_id
    )
    assert original.status == "COMPLETED" and len(original.task_ids) == 2
    leftovers = [item for item in current.runtime.details.lots if item.source_spec_id == "cut"]
    assert len(leftovers) == 2
    assert all(
        item.available.fraction() == 80 and item.produced_by_execution_id == shared.execution_id
        for item in leftovers
    )
    lot = leftovers[0]
    added = event(
        current,
        "shared-leftover-add",
        "ADD_RECIPE",
        {"recipes": [{"id": target.recipe_id, "name": target.name}]},
        at=runtime.clock.offset_sec,
    )
    assert runtime.apply_event(added).status == "APPLIED"
    current = runtime.get("shared-retry")
    instance = current.menu[-1]
    covered = tuple(
        stable_id("task", instance.recipe_instance_id.root, operation)
        for operation in ("wash", "cut")
    )
    rule = InventoryRule(
        rule_id="synthetic-shared-leftover-inventory",
        target_task_ids=covered,
        source_spec_id=lot.spec_id,
        quantity=RationalAmount(numerator=50),
        unit="g",
        evidence_refs=("synthetic:shared-leftover-closed-preparation",),
        knowledge_version=current.runtime.knowledge_version,
        source_kind="SYNTHETIC",
        approved=True,
        max_age_sec=3600,
    )
    with runtime.store.transaction() as tx:
        RuntimeRepository(tx).save(
            current.model_copy(update={"inventory_rules": (rule,)}),
            expected_revision=current.runtime.state_revision,
        )
    result = planning.drain("shared-retry")
    assert result.status == "PUBLISHED", result
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
        assert (
            ScheduleValidator()
            .validate(runtime.knowledge, problem.runtime, problem, solved.candidate)
            .valid
        )
    simulator.advance(result.plan.validated.candidate.metrics.makespan_sec)
    after = runtime.get("shared-retry")
    assert (
        next(item for item in after.runtime.executions if item.execution_id == shared.execution_id)
        == original
    )
    assert (
        next(
            item for item in after.runtime.details.lots if item.lot_id == lot.lot_id
        ).available.fraction()
        == 30
    )
    assert not any(
        set(covered) & {task.root for task in item.task_ids} for item in after.runtime.executions
    )
    fulfillment = next(
        item
        for item in after.runtime.details.inventory_fulfillments
        if set(covered) == {task.root for task in item.task_ids}
    )
    assert fulfillment.status == "COMMITTED"
    assert fulfillment.supply.lot_claims[0].consumed.fraction() == 50
    before_replay = after
    assert runtime.apply_event(added).status == "APPLIED"
    assert runtime.get("shared-retry") == before_replay
