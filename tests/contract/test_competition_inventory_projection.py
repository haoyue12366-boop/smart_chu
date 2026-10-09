"""明确合成库存边界，经真实计划发布与物料账检查供应投影，非正式菜谱验收。"""

import pytest

from app.api.competition_steps import projected_tasks, step_description, step_kind
from app.scheduling.worker import SolverWorker
from app.storage.repositories import RuntimeRepository
from app.validation.competition_facts import projection_facts
from tests.integration.test_inventory_substitution import stock_service
from tests.integration.test_running_thermal_batch import running_h02
from tests.runtime_support import event


@pytest.mark.parametrize("frozen", [False, True])
def test_inventory_steps_keep_supply_identity_without_new_processing(tmp_path, frozen):
    runtime, planning, simulator, _, rules = stock_service(tmp_path)
    published = planning.drain("inventory")
    assert published.status == "PUBLISHED", published
    if frozen:
        simulator.advance(120)
        current = runtime.get("inventory")
        published = planning.apply_event(
            event(
                current,
                "projection-after-inventory-consumed",
                "DELAY_RECIPE",
                {
                    "recipe_instance_id": current.menu[-1].recipe_instance_id,
                    "earliest_start_sec": 120,
                },
                at=120,
            )
        )
        assert published.status == "PUBLISHED", published
    before = runtime.get("inventory")
    with runtime.store.engine.connect() as tx:
        problem = RuntimeRepository(tx).problem("inventory", published.plan.plan_version)
    steps = projected_tasks(published.plan, problem)
    facts = {fact.task.task_id: fact for fact in projection_facts(published.plan, problem)}
    assert {step.task.task_id for step in steps} == set(facts)
    for step in steps:
        fact = facts[step.task.task_id]
        assert (step.interval.start_sec, step.interval.end_sec, step.owner) == (
            fact.start,
            fact.end,
            fact.owner,
        )
        if step.task.task_id in rules[0].target_task_ids:
            assert step.resources == ()
            assert step_kind(step, problem)[0] == 1
            assert step_description(step, problem).startswith("已有合格备料满足：")
            assert step.interval.start_sec == step.interval.end_sec
    assert runtime.get("inventory") == before


def test_real_frozen_stove_step_keeps_actual_device_choice(tmp_path):
    # 真实固定 H02 发布，动态过程为明确模拟反馈；不修改已发布工艺。
    with SolverWorker() as worker:
        runtime, planning, _, _, _, _ = running_h02(tmp_path, worker)
        current = runtime.get("flow")
        published = planning.apply_event(
            event(
                current,
                "frozen-projection-preference",
                "DELAY_RECIPE",
                {
                    "recipe_instance_id": current.menu[-1].recipe_instance_id,
                    "earliest_start_sec": current.runtime.now_offset_sec,
                },
                at=runtime.clock.offset_sec,
            )
        )
        assert published.status == "PUBLISHED", published
        with runtime.store.engine.connect() as tx:
            problem = RuntimeRepository(tx).problem("flow", published.plan.plan_version)
        fact = next(
            record
            for record in problem.fixed_executions
            if any(
                span.resource.physical_resource_id in {"stove_1", "stove_2"}
                for span in record.resource_spans
            )
        )
        actual = next(
            span.resource
            for span in fact.resource_spans
            if span.resource.physical_resource_id in {"stove_1", "stove_2"}
        )
        steps = projected_tasks(published.plan, problem)
        checked = 0
        for step in steps:
            if step.task.task_id not in fact.task_ids:
                continue
            original = next(
                (
                    use
                    for use in step.task.operation.resource_requirements
                    if use.resource_id == "stove_choice"
                ),
                None,
            )
            if original is not None:
                chosen = next(use for use in step.resources if use.resource_type == "DEVICE")
                assert chosen.resource_id == actual.resource_id
                assert chosen.physical_resource_id == actual.physical_resource_id
                checked += 1
        assert checked > 0
