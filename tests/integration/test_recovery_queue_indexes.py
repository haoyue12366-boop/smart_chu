"""真实旧库升级、待办筛选、索引查询计划及事务回滚。"""

import pytest
from sqlalchemy import event as sql_event

from app.config import AppSettings
from app.domain.runtime_planning import RuntimePlanningResult
from app.runtime.clock import SimulationClock
from app.runtime.service import RuntimeService
from app.services.container import ServiceContainer
from app.storage.competition_tasks import HttpRequestRecord, HttpRequestRepository
from app.storage.repositories import RuntimeRepository
from app.storage.unit_of_work import UnitOfWork
from tests.integration.test_menu_events_replanning import start_event
from tests.runtime_support import ORIGIN, policy, synthetic_knowledge


def test_legacy_queue_upgrade_preserves_records_and_uses_partial_indexes(tmp_path):
    store = UnitOfWork(tmp_path / "legacy.sqlite3")
    store.migrate("0008_simulation_control")
    runtime = RuntimeService(store, synthetic_knowledge(), SimulationClock(ORIGIN))
    idle = runtime.create_session("idle", "SIMULATED", ORIGIN, policy())
    queued = runtime.create_session("queued", "SIMULATED", ORIGIN, policy())
    queued = queued.model_copy(update={"status": "ACTIVE"})
    ended = runtime.create_session("ended", "SIMULATED", ORIGIN, policy())
    with store.transaction() as tx:
        repo = RuntimeRepository(tx)
        repo.save(queued.model_copy(update={"requires_replan": True}), expected_revision=0)
        repo.save(
            ended.model_copy(update={"requires_replan": True, "status": "ENDED"}),
            expected_revision=0,
        )
        requests = HttpRequestRepository(tx)
        for name in ("unprocessed", "pending", "completed"):
            requests.reserve(
                HttpRequestRecord(
                    request_id=name,
                    payload_hash=name,
                    session_id="queued",
                    event=start_event(queued),
                )
            )
        requests.finish("pending", RuntimePlanningResult(status="PENDING", budget_ms=2400))
        requests.finish("completed", RuntimePlanningResult(status="NO_REPLAN", budget_ms=2400))
    with store.engine.connect() as tx:
        before = tx.exec_driver_sql("SELECT * FROM cooking_sessions ORDER BY session_id").all()
        receipts = tx.exec_driver_sql("SELECT * FROM http_requests ORDER BY request_id").all()
    store.migrate()
    with store.engine.connect() as tx:
        assert (
            tx.exec_driver_sql("SELECT * FROM cooking_sessions ORDER BY session_id").all() == before
        )
        assert (
            tx.exec_driver_sql("SELECT * FROM http_requests ORDER BY request_id").all() == receipts
        )
        assert RuntimeRepository(tx).get("idle") == idle
    container = ServiceContainer(AppSettings(database_path=store.path, language_enabled=False))
    container.store = store
    statements = []

    def observe(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("SELECT"):
            statements.append((statement, parameters))

    sql_event.listen(store.engine, "before_cursor_execute", observe)
    assert container.pending_sessions() == ("queued",)
    with store.engine.connect() as tx:
        assert {r.request_id for r in HttpRequestRepository(tx).pending()} == {
            "unprocessed",
            "pending",
        }
    sql_event.remove(store.engine, "before_cursor_execute", observe)
    with store.engine.connect() as tx:
        for table, index in (
            ("cooking_sessions", "ix_sessions_pending_recovery"),
            ("http_requests", "ix_http_pending_recovery"),
        ):
            statement, parameters = next(
                item for item in statements if "json_extract" in item[0] and table in item[0]
            )
            details = [
                r[3] for r in tx.exec_driver_sql("EXPLAIN QUERY PLAN " + statement, parameters)
            ]
            assert any(index in detail for detail in details), details
    with pytest.raises(RuntimeError, match="rollback"):
        with store.transaction() as tx:
            RuntimeRepository(tx).save(queued, expected_revision=0)
            HttpRequestRepository(tx).finish(
                "unprocessed", RuntimePlanningResult(status="NO_REPLAN", budget_ms=2400)
            )
            raise RuntimeError("rollback")
    assert container.pending_sessions() == ("queued",)
    with store.engine.connect() as tx:
        assert {r.request_id for r in HttpRequestRepository(tx).pending()} == {
            "unprocessed",
            "pending",
        }
    store.close()
