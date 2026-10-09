"""合成 SQLite 回归：一桌启动窗口失效不能阻断另一桌；库存满足仍能推进。"""

import json

import pytest
from sqlalchemy import select

from app.config import AppSettings
from app.domain.events import EventSource
from app.domain.runtime_clock import ScheduleClockState
from app.runtime.schedule_clock import ClockExecutionService
from app.runtime.scheduled_actions import PublishedActions
from app.services.container import ServiceContainer
from app.services.recovery_guard import isolated_recovery
from app.storage import models
from app.storage.repositories import RuntimeRepository
from tests.integration import test_inventory_substitution as stock
from tests.integration.test_schedule_clock import clock_knowledge, clock_service, tick
from tests.runtime_support import ORIGIN, event


def test_expired_clock_window_is_isolated_and_other_table_finishes(tmp_path):
    knowledge = clock_knowledge()
    recipes = tuple(
        recipe.model_copy(
            update={
                "dependencies": tuple(
                    dep.model_copy(update={"max_lag_sec": 0}) for dep in recipe.dependencies
                )
            }
        )
        for recipe in knowledge.recipes
    )
    runtime, planner, _ = clock_service(
        tmp_path, knowledge=knowledge.model_copy(update={"recipes": recipes})
    )
    policy = runtime.get("clock").policy
    healthy = runtime.create_session("z-healthy", "SCHEDULE_CLOCK", ORIGIN, policy)
    result = planner.apply_event(
        event(
            healthy,
            "healthy-start",
            "START_SESSION",
            {"recipes": [{"id": recipes[0].recipe_id, "name": recipes[0].name}]},
        )
    )
    assert result.status == "PUBLISHED", result
    before = tick(runtime, 60)
    running = before.runtime.executions[0]
    binding = next(b for b in before.bindings if running.task_ids[0] in b.assignment.task_ids)
    payload = PublishedActions(runtime, "clock", namespace="clock").payload(
        before, binding, running.task_ids, running.execution_id.root, completed=True
    )
    completed = runtime.apply_event(
        event(
            before,
            "prep-end",
            "OPERATION_COMPLETED",
            payload,
            at=120,
        )
    )
    assert completed.status == "APPLIED", completed
    current = runtime.get("clock")
    # 明确构造已错过零间隔窗口的持久恢复输入，不修改任何正式知识。
    with runtime.store.transaction() as tx:
        RuntimeRepository(tx).save(
            current.model_copy(
                update={
                    "runtime": current.runtime.model_copy(update={"now_offset_sec": 121}),
                    "schedule_clock": current.schedule_clock.model_copy(
                        update={"processed_until_sec": 121}
                    ),
                }
            ),
            expected_revision=current.runtime.state_revision,
        )
    services = ServiceContainer(AppSettings(database_path=tmp_path / "clock.sqlite"))
    services.store, services.clock, services.policy = runtime.store, runtime.clock, policy
    services.runtimes[runtime.knowledge.release.release_id] = runtime
    services.planners[runtime.knowledge.release.release_id] = planner
    assert services.clock_sessions() == ("clock", "z-healthy")
    runtime.clock.advance(121)
    services.recover_pending()
    failed = runtime.get("clock")
    assert failed.dispatch_blocked and "启动窗口已关闭" in failed.last_planning_failure
    assert failed.runtime.executions == current.runtime.executions
    healthy = runtime.get("z-healthy")
    assert any(record.status == "COMPLETED" for record in healthy.runtime.executions)
    failed_revision = failed.runtime.state_revision

    runtime.clock.advance(420)
    services.recover_pending()
    assert runtime.get("clock").runtime.state_revision == failed_revision
    healthy = runtime.get("z-healthy")
    assert len(healthy.runtime.executions) == 3
    assert all(record.status == "COMPLETED" for record in healthy.runtime.executions)
    with runtime.store.engine.connect() as tx:
        messages = [
            json.loads(raw)
            for raw in tx.execute(
                select(models.notification_stream.c.body).where(
                    models.notification_stream.c.session_id == "z-healthy"
                )
            ).scalars()
        ]
    assert len([item for item in messages if item["kind"] == "OPERATION_COMPLETED"]) == 3
    runtime.store.close()


def test_stale_recovery_error_cannot_pause_a_newly_published_plan(tmp_path):
    runtime, planner, _ = clock_service(tmp_path)
    with isolated_recovery(runtime.store, "clock"):
        # 恢复作业持有 v1 时，前台请求完成一次合法重排并发布 v2。
        current = tick(runtime, 60)
        result = planner.apply_event(event(current, "newer-request", "REPLAN_REQUESTED", {}, at=60))
        assert result.status == "PUBLISHED" and result.plan.plan_version == 2
        repaired = runtime.get("clock")
        assert not repaired.dispatch_blocked and repaired.last_planning_failure is None
        raise ValueError("旧计划的启动窗口失效")
    assert runtime.get("clock") == repaired
    with runtime.store.engine.connect() as tx:
        audits = tx.execute(select(models.audits.c.body)).scalars()
        assert not any(json.loads(body).get("kind") == "RECOVERY_FAILED" for body in audits)
    runtime.store.close()


@pytest.mark.parametrize("guarded", [False, True])
def test_clock_uses_inventory_fulfillment_without_fabricating_preparation(
    tmp_path, monkeypatch, guarded
):
    # 原测试工艺本身就是合成；此处连知识外壳也明确使用合成发布，避免依赖旧快照。
    monkeypatch.setattr(stock, "p4_knowledge", clock_knowledge)
    runtime, planner, _, lot, rules = stock.stock_service(tmp_path)
    published = planner.drain("inventory")
    assert published.status == "PUBLISHED", published
    current = runtime.get("inventory")
    clocked = current.model_copy(
        update={
            "runtime": current.runtime.model_copy(
                update={"execution_mode": EventSource.SCHEDULE_CLOCK}
            ),
            "schedule_clock": ScheduleClockState(
                started_at=runtime.clock.now(), start_offset_sec=120, processed_until_sec=120
            ),
        }
    )
    if guarded:
        # 同时覆盖启用连续工艺派发保护的旧策略。
        clocked = clocked.model_copy(
            update={
                "policy": clocked.policy.model_copy(
                    update={"dispatch_guard_policy_id": "TIGHT_HUMAN_V1"}
                )
            }
        )
    with runtime.store.transaction() as tx:
        RuntimeRepository(tx).save(clocked, expected_revision=current.runtime.state_revision)
    actions = PublishedActions(runtime, "inventory", namespace="clock").planned(clocked, 120)
    assert any(action.kind == "OPERATION_STARTED" and action.at == 120 for action in actions)
    runtime.clock.advance(180)
    after = ClockExecutionService(runtime).advance("inventory")
    assert len(after.runtime.executions) == 3
    assert all(record.status == "COMPLETED" for record in after.runtime.executions)
    assert not any(
        set(record.task_ids).intersection(rules[0].target_task_ids)
        for record in after.runtime.executions
    )
    remaining = next(item for item in after.runtime.details.lots if item.lot_id == lot.lot_id)
    assert remaining.available.fraction() == 30
    assert after.runtime.details.inventory_fulfillments[-1].status == "COMMITTED"
    runtime.store.close()
