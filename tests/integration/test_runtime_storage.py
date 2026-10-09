"""真实 SQLite 事务、迁移与事件唯一性；不连接外部服务。"""

import pytest
from sqlalchemy import inspect, text

from app.storage.unit_of_work import UnitOfWork


def test_migration_is_repeatable_and_keeps_history(tmp_path):
    store = UnitOfWork(tmp_path / "runtime.sqlite")
    store.migrate()
    with store.transaction() as tx:
        tx.execute(
            text("INSERT INTO audit_records (audit_id, session_id, body) VALUES ('a', NULL, '{}')")
        )
    store.migrate()
    with store.transaction() as tx:
        assert tx.execute(text("SELECT count(*) FROM audit_records")).scalar_one() == 1
        assert tx.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        assert tx.execute(text("PRAGMA journal_mode")).scalar_one() == "wal"
    store.close()


def test_failed_transaction_does_not_write_partial_history(tmp_path):
    store = UnitOfWork(tmp_path / "runtime.sqlite")
    store.migrate()
    with pytest.raises(RuntimeError), store.transaction() as tx:
        tx.execute(
            text("INSERT INTO audit_records (audit_id, session_id, body) VALUES ('a', NULL, '{}')")
        )
        raise RuntimeError("提交前中断")
    with store.transaction() as tx:
        assert tx.execute(text("SELECT count(*) FROM audit_records")).scalar_one() == 0
    store.close()


def test_version_upgrade_preserves_history_and_adds_lookup_indexes(tmp_path):
    store = UnitOfWork(tmp_path / "upgrade.sqlite")
    store.migrate("0001_runtime")
    with store.transaction() as tx:
        tx.execute(
            text("INSERT INTO audit_records (audit_id, body) VALUES ('old', '历史不能删除')")
        )
    store.migrate()
    with store.engine.connect() as tx:
        assert (
            tx.scalar(text("SELECT body FROM audit_records WHERE audit_id='old'")) == "历史不能删除"
        )
        assert (
            tx.scalar(text("SELECT version_num FROM alembic_version")) == "0008_simulation_control"
        )
        assert "future_material_allocations" in inspect(tx).get_table_names()
        assert "inventory_fulfillments" in inspect(tx).get_table_names()
        assert "competition_tasks" in inspect(tx).get_table_names()
        assert "notification_stream" in inspect(tx).get_table_names()
        assert "client_body" in {c["name"] for c in inspect(tx).get_columns("http_requests")}
        indexes = inspect(tx).get_indexes("runtime_events")
        assert any(i["column_names"] == ["session_id"] for i in indexes)


def test_sqlite_write_lock_has_finite_wait_and_no_event_side_effect(tmp_path):
    import time

    from sqlalchemy.exc import OperationalError

    from app.runtime.clock import SimulationClock
    from app.runtime.service import RuntimeService
    from tests.runtime_support import ORIGIN, event, policy, synthetic_knowledge

    store = UnitOfWork(tmp_path / "lock.sqlite")
    store.migrate()
    runtime = RuntimeService(store, synthetic_knowledge(), SimulationClock(ORIGIN))
    session = runtime.create_session("lock", "SIMULATED", ORIGIN, policy())
    start = event(
        session,
        "locked-event",
        "START_SESSION",
        {"recipes": [{"id": "synthetic-0", "name": "合成腌制0"}]},
    )
    with store.transaction() as tx:
        tx.execute(text("INSERT INTO audit_records (audit_id, body) VALUES ('lock', '{}')"))
        before = time.monotonic()
        with pytest.raises((OperationalError, TimeoutError)):
            runtime.apply_event(start)
        assert time.monotonic() - before < 1.2
    with store.engine.connect() as tx:
        assert tx.scalar(text("SELECT count(*) FROM runtime_events")) == 0
    assert runtime.get("lock").runtime.state_revision == 0
    assert runtime.apply_event(start).status == "APPLIED"


def test_saving_menu_cannot_reuse_another_sessions_identity_and_rolls_back(tmp_path):
    from app.domain.scheduling_problem import RecipeInstance
    from app.runtime.clock import SimulationClock
    from app.runtime.service import RuntimeService
    from app.storage.repositories import RuntimeRepository, StateConflict
    from tests.runtime_support import ORIGIN, policy, synthetic_knowledge

    store = UnitOfWork(tmp_path / "identities.sqlite")
    store.migrate()
    runtime = RuntimeService(store, synthetic_knowledge(), SimulationClock(ORIGIN))
    first = runtime.create_session("first", "SIMULATED", ORIGIN, policy())
    second = runtime.create_session("second", "SIMULATED", ORIGIN, policy())
    item = RecipeInstance(
        recipe_instance_id="deliberate-collision", recipe_id="synthetic-0", name="合成腌制0"
    )
    with store.transaction() as tx:
        RuntimeRepository(tx).save(
            first.model_copy(update={"menu": (item,)}), expected_revision=0, expected_plan=0
        )
    with pytest.raises(StateConflict), store.transaction() as tx:
        RuntimeRepository(tx).save(
            second.model_copy(
                update={
                    "menu": (item,),
                    "runtime": second.runtime.model_copy(update={"state_revision": 1}),
                }
            ),
            expected_revision=0,
            expected_plan=0,
        )
    assert runtime.get("second") == second
    assert runtime.get("first").menu == (item,)
    store.close()
