"""P5 生命周期使用真实快照、运行库和常驻工作进程。"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest
from fastapi.testclient import TestClient

from app.config import AppSettings
from app.main import create_app


def settings(tmp_path: Path) -> AppSettings:
    return AppSettings(database_path=tmp_path / "runtime.sqlite3", language_enabled=False)


def test_cold_start_ready_without_neo4j_and_shutdown(tmp_path: Path) -> None:
    app = create_app(settings(tmp_path))
    with TestClient(app) as client:
        assert client.get("/health/live").json()["status"] == "live"
        assert client.get("/health/ready").status_code == 200
        assert len(client.get("/api/v1/recipes").json()["recipes"]) == 100
        worker = app.state.container.worker
        assert worker.is_alive
    assert not worker.is_alive


def test_online_start_keeps_full_runtime_view_without_duplicate_snapshot_cache(tmp_path):
    app = create_app(settings(tmp_path))
    with TestClient(app) as client:
        services = app.state.container
        assert len(services.knowledge.recipes) == 100
        assert services.repository._cache == {}
        assert len(client.get("/api/v1/recipes").json()["recipes"]) == 100
        assert client.get("/health/ready").status_code == 200


def test_corrupt_release_does_not_claim_ready(tmp_path: Path) -> None:
    app = create_app(settings(tmp_path).model_copy(update={"release_root": tmp_path / "missing"}))
    with pytest.raises((FileNotFoundError, ValueError)):
        with TestClient(app):
            pass


def test_unknown_session_error_has_trace_and_no_stack(tmp_path: Path) -> None:
    with TestClient(create_app(settings(tmp_path))) as client:
        result = client.get("/api/v1/sessions/missing")
        assert result.status_code == 404
        assert result.json()["error"]["request_id"]
        assert "Traceback" not in result.text


def test_shutdown_waits_for_inflight_recovery_before_closing_storage(tmp_path, monkeypatch):
    """真实生命周期与工作进程；屏障只控制后台作业的观察时序。"""
    app = create_app(settings(tmp_path).model_copy(update={"recovery_interval_sec": 0.01}))
    services = app.state.container
    entered, release, closed = Event(), Event(), Event()
    recover, close = services.recover_pending, services.close

    def paused_recovery():
        entered.set()
        assert release.wait(5), "测试屏障未释放"
        recover()

    def observed_close():
        closed.set()
        close()

    monkeypatch.setattr(services, "recover_pending", paused_recovery)
    monkeypatch.setattr(services, "close", observed_close)
    client = TestClient(app)
    client.__enter__()
    assert entered.wait(5)
    with ThreadPoolExecutor(max_workers=1) as pool:
        shutdown = pool.submit(client.__exit__, None, None, None)
        try:
            assert not closed.wait(0.2), "后台恢复尚未结束就关闭了共享资源"
        finally:
            release.set()
            shutdown.result(timeout=10)
    assert closed.is_set()
    assert not services.worker.is_alive
