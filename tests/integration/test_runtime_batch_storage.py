"""批量持久化减少 SQL 次数，仍原子保留身份冲突和历史保护。"""

import pytest
from sqlalchemy import event as sql_event

from app.domain.policy import SchedulingPolicy
from app.domain.scheduling_problem import RecipeInstance
from app.runtime.clock import SimulationClock
from app.runtime.service import RuntimeService
from app.storage.repositories import RuntimeRepository, StateConflict
from app.storage.unit_of_work import UnitOfWork
from tests.integration.test_schedule_clock import clock_knowledge
from tests.runtime_support import ORIGIN


def test_large_state_uses_one_recipe_insert_and_unchanged_rows_are_not_rewritten(tmp_path):
    store = UnitOfWork(tmp_path / "batch.sqlite3")
    store.migrate()
    runtime = RuntimeService(store, clock_knowledge(), SimulationClock(ORIGIN))
    original = runtime.create_session(
        "batch", "MANUAL_CONFIRM", ORIGIN, SchedulingPolicy(policy_version="synthetic-batch")
    )
    items = tuple(
        RecipeInstance(recipe_instance_id=f"synthetic-{i}", recipe_id="clock-0", name="合成存储项")
        for i in range(100)
    )
    state = original.model_copy(update={"menu": items})
    inserts = []

    def count_sql(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO recipe_instances"):
            inserts.append(statement)

    sql_event.listen(store.engine, "before_cursor_execute", count_sql)
    try:
        with store.transaction() as tx:
            RuntimeRepository(tx).save(state, expected_revision=0, expected_plan=0)
        assert runtime.get("batch") == state
        assert len(inserts) == 1
        with store.transaction() as tx:
            RuntimeRepository(tx).save(state, expected_revision=0, expected_plan=0)
        assert len(inserts) == 1
        with pytest.raises(StateConflict), store.transaction() as tx:
            RuntimeRepository(tx).save(state, expected_revision=10, expected_plan=0)
        assert runtime.get("batch") == state
    finally:
        sql_event.remove(store.engine, "before_cursor_execute", count_sql)
        store.close()


def test_duplicate_identity_in_one_batch_keeps_previous_behavior(tmp_path):
    store = UnitOfWork(tmp_path / "duplicates.sqlite3")
    store.migrate()
    runtime = RuntimeService(store, clock_knowledge(), SimulationClock(ORIGIN))
    original = runtime.create_session(
        "duplicate", "MANUAL_CONFIRM", ORIGIN, SchedulingPolicy(policy_version="synthetic-batch")
    )
    item = RecipeInstance(recipe_instance_id="repeated", recipe_id="clock-0", name="原名称")
    updated = item.model_copy(update={"name": "新名称"})
    state = original.model_copy(update={"menu": (item, updated)})
    with store.transaction() as tx:
        RuntimeRepository(tx).save(state, expected_revision=0, expected_plan=0)
    assert runtime.get("duplicate") == state
    store.close()
