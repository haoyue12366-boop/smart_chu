"""合成慢启动只延迟预热；就绪、传输和后续求解仍使用真实进程。"""

import time

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import AppSettings
from app.main import create_app
from app.scheduling.json_worker import _json_worker_loop
from app.scheduling.worker import SolverWorker
from app.validation.schedule import ScheduleValidator
from tests.unit.test_cp_sat_model import deadline
from tests.unit.test_schedule_validator import example


def delayed_json_worker(inbox, outbox):
    time.sleep(11)
    _json_worker_loop(inbox, outbox)


def unresponsive_startup(inbox, outbox):
    time.sleep(60)


def test_application_waits_for_real_worker_beyond_ten_seconds(tmp_path, monkeypatch):
    monkeypatch.setenv("SMART_COOKING_SOLVER_STARTUP_TIMEOUT_SEC", "30")
    monkeypatch.setenv("SMART_COOKING_DATABASE_PATH", str(tmp_path / "startup.sqlite3"))
    app = create_app(AppSettings.from_environment())
    worker = app.state.container.worker
    # 仅控制启动速度，不伪造 ready 消息、知识、校验或求解结果。
    worker._target = delayed_json_worker
    with TestClient(app) as client:
        assert worker.startup_ms >= 11_000
        assert client.get("/health/ready").json() == {"status": "ready"}
        assert len(client.get("/api/v1/recipes").json()["recipes"]) == 100
        knowledge, state, problem, candidate = example()
        result = worker.solve(problem, candidate, deadline())
        assert result.status == "OPTIMAL"
        assert ScheduleValidator().validate(knowledge, state, problem, result.candidate).valid
    assert not worker.is_alive


def test_request_deadline_still_bounds_long_startup_allowance():
    _, _, problem, _ = example()
    worker = SolverWorker(target=unresponsive_startup, startup_timeout_sec=30)
    try:
        started = time.monotonic()
        result = worker.solve(problem, None, deadline(0.05))
        assert result.status == "UNKNOWN"
        assert result.candidate is None
        assert time.monotonic() - started < 1
        assert worker.is_alive
    finally:
        worker.close()


def test_startup_timeout_is_shared_across_attempts_and_reaps_worker():
    worker = SolverWorker(target=unresponsive_startup, startup_timeout_sec=0.4)
    try:
        assert not worker.warmup(deadline(0.05))
        assert worker.is_alive
        time.sleep(0.45)
        assert not worker.warmup(deadline(5))
        assert not worker.is_alive
        assert worker.process_id is None
    finally:
        worker.close()


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "invalid"])
def test_environment_rejects_invalid_startup_allowance(value, monkeypatch):
    monkeypatch.setenv("SMART_COOKING_SOLVER_STARTUP_TIMEOUT_SEC", value)
    with pytest.raises(ValidationError):
        AppSettings.from_environment()


def test_startup_failure_does_not_publish_ready_and_closes_worker(tmp_path, monkeypatch):
    monkeypatch.setenv("SMART_COOKING_SOLVER_STARTUP_TIMEOUT_SEC", "0.05")
    monkeypatch.setenv("SMART_COOKING_DATABASE_PATH", str(tmp_path / "failed.sqlite3"))
    app = create_app(AppSettings.from_environment())
    services = app.state.container
    services.worker._target = unresponsive_startup
    with pytest.raises(TimeoutError, match="求解进程未就绪"):
        with TestClient(app):
            pass
    assert not services.ready
    assert not services.worker.is_alive
