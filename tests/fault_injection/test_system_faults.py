"""完整 Windows 应用与真实 SQLite/IPC；只在明确故障边界注入替身。"""

import json
import os
import shutil
import socket
import time
from contextlib import closing

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.competition_adapter import CompetitionAdapter
from app.config import AppSettings
from app.domain.candidates import stable_id
from app.main import create_app
from app.runtime.notifications import NotificationService
from app.scheduling.worker import SolverWorker
from app.services.publishing import PlanPublisher
from app.storage import models
from app.storage.competition_tasks import HttpRequestRepository
from app.storage.repositories import RuntimeRepository
from app.validation.schedule import ScheduleValidator
from tests.contract.test_competition_endpoint import STEAK
from tests.contract.test_language_events import FixedProvider
from tests.fault_injection.stream_support import WindowsApiProcess
from tests.fault_injection.test_solver_worker_failure import crash_worker, hung_worker


@pytest.fixture
def environment(tmp_path):
    settings = AppSettings(
        database_path=tmp_path / "system.sqlite3",
        recovery_interval_sec=60,
        language_enabled=True,
        language_archive_path=tmp_path / "explicit-local-intent",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        yield app, client, settings


def record_timing(record_testsuite_property, name, fault_ms, recovery_ms, **facts):
    record_testsuite_property(
        "p6_fault:" + name,
        json.dumps(
            {"fault_request_ms": fault_ms, "recovery_ms": recovery_ms, **facts}, ensure_ascii=False
        ),
    )


def request(client, task, key="one"):
    began = time.perf_counter_ns()
    response = client.post(
        "/api/competition/plan",
        params={"task_id": task},
        json=[STEAK],
        headers={"Idempotency-Key": key},
    )
    return response, (time.perf_counter_ns() - began) / 1_000_000


def receipt(app, task, key="one"):
    identity = stable_id("competition-http", task, key)
    with app.state.container.store.engine.connect() as tx:
        stored = HttpRequestRepository(tx).get(identity)
    assert stored is not None
    return stored


def independent_persisted_scan(app, task, key="one"):
    stored = receipt(app, task, key)
    session = app.state.container.runtime.get(stored.session_id)
    with app.state.container.store.engine.connect() as tx:
        repo = RuntimeRepository(tx)
        plan = repo.plan(stored.session_id, session.runtime.current_plan_version)
        problem = repo.problem(stored.session_id, session.runtime.current_plan_version)
    assert plan is not None
    proof = ScheduleValidator().validate(
        app.state.container.knowledge, problem.runtime, problem, plan.validated.candidate
    )
    assert proof.valid
    return session


def counts(app):
    with app.state.container.store.engine.connect() as tx:
        return {
            label: tx.scalar(select(func.count()).select_from(table))
            for label, table in (
                ("sessions", models.sessions),
                ("events", models.events),
                ("plans", models.plans),
                ("notifications", models.notifications),
            )
        }


def install_worker(app, worker):
    services = app.state.container
    services.worker.close()
    services.worker = worker
    assert worker.warmup()
    for planner in services.planners.values():
        planner.solver = worker


@pytest.mark.parametrize("fault", ["exit", "hang"])
def test_real_worker_failure_has_no_partial_http_success_and_next_request_recovers(
    environment, fault, record_testsuite_property
):
    app, client, _ = environment
    broken = SolverWorker(target=crash_worker if fault == "exit" else hung_worker)
    install_worker(app, broken)
    response, elapsed = request(client, "solver-" + fault)
    assert response.status_code in {200, 503}, response.text
    if response.status_code == 200:
        independent_persisted_scan(app, "solver-" + fault)
    else:
        assert "overview" not in response.json()
        stored = receipt(app, "solver-" + fault)
        state = app.state.container.runtime.get(stored.session_id)
        assert len(state.menu) == 1 and state.runtime.state_revision == 1
        if state.runtime.current_plan_version == 0:
            assert state.dispatch_blocked and counts(app)["plans"] == 0
        else:
            # 合法 Greedy 回退可能已发布，但没有串行参考时官方响应仍须诚实失败。
            assert state.runtime.current_plan_version == 1 and counts(app)["plans"] == 1
            independent_persisted_scan(app, "solver-" + fault)
    began = time.perf_counter_ns()
    install_worker(app, SolverWorker())
    recovered, normal_ms = request(client, "healthy-after-" + fault)
    recovery_ms = (time.perf_counter_ns() - began) / 1_000_000
    assert recovered.status_code == 200, recovered.text
    independent_persisted_scan(app, "healthy-after-" + fault)
    record_timing(
        record_testsuite_property,
        "solver_" + fault,
        elapsed,
        recovery_ms,
        http_status=response.status_code,
        following_request_ms=normal_ms,
        request_budget_ms=4200,
        fault_request_budget_exceeded=elapsed > 4200,
    )


def test_real_sqlite_write_lock_rejects_without_half_commit_and_recovers(
    environment, record_testsuite_property
):
    app, client, _ = environment
    before = counts(app)
    with app.state.container.store.engine.connect() as locked:
        locked.exec_driver_sql("BEGIN IMMEDIATE")
        failed, elapsed = request(client, "locked")
        assert failed.status_code == 503, failed.text
        assert "overview" not in failed.json()
        locked.rollback()
    assert counts(app) == before
    recovered, recovery_ms = request(client, "locked")
    assert recovered.status_code == 200, recovered.text
    state = independent_persisted_scan(app, "locked")
    assert len(state.menu) == 1 and state.runtime.state_revision == 1
    record_timing(record_testsuite_property, "sql_lock", elapsed, recovery_ms, half_commit=False)


@pytest.mark.parametrize("boundary", ["before_commit", "after_commit", "response_projection"])
def test_commit_boundaries_preserve_one_menu_and_one_publication(
    environment, monkeypatch, boundary, record_testsuite_property
):
    app, client, _ = environment
    if boundary == "before_commit":
        original = NotificationService.persist

        def injected(self, *args, **kwargs):
            original(self, *args, **kwargs)
            raise InterruptedError("explicit synthetic fault before SQL commit")

        owner, attribute = NotificationService, "persist"
    elif boundary == "after_commit":
        original = PlanPublisher._publish

        def injected(self, *args, **kwargs):
            original(self, *args, **kwargs)
            raise ConnectionError("explicit synthetic response loss after SQL commit")

        owner, attribute = PlanPublisher, "_publish"
    else:
        original = CompetitionAdapter.to_response

        def injected(self, *args, **kwargs):
            raise TimeoutError("explicit synthetic projection interruption after commit")

        owner, attribute = CompetitionAdapter, "to_response"
    monkeypatch.setattr(owner, attribute, injected)
    first, elapsed = request(client, boundary)
    stored = receipt(app, boundary)
    state = app.state.container.runtime.get(stored.session_id)
    assert len(state.menu) == 1 and state.runtime.state_revision == 1
    if boundary == "before_commit":
        assert first.status_code == 503 and state.runtime.current_plan_version == 0
        assert counts(app)["plans"] == counts(app)["notifications"] == 0
    else:
        assert state.runtime.current_plan_version == 1 and counts(app)["plans"] == 1
    monkeypatch.setattr(owner, attribute, original)
    began = time.perf_counter_ns()
    if boundary == "before_commit":
        _, planner = app.state.container.for_session(stored.session_id)
        outcome = planner.drain(stored.session_id)
        assert outcome.status == "PUBLISHED", outcome.model_dump_json()
    restored, _ = request(client, boundary)
    recovery_ms = (time.perf_counter_ns() - began) / 1_000_000
    assert restored.status_code == 200, restored.text
    after = independent_persisted_scan(app, boundary)
    assert len(after.menu) == 1 and after.runtime.state_revision == 1
    assert after.runtime.current_plan_version == 1 and counts(app)["plans"] == 1
    before_retry = counts(app)
    assert request(client, boundary)[0].text == restored.text
    assert counts(app) == before_retry
    record_timing(record_testsuite_property, boundary, elapsed, recovery_ms, business_fact_count=1)


@pytest.mark.parametrize("fault", ["changed_snapshot_bytes", "mixed_manifest_version"])
def test_corrupt_or_mixed_release_cannot_be_ready_and_original_still_starts(
    tmp_path, fault, record_testsuite_property
):
    settings = AppSettings(database_path=tmp_path / "corrupt.sqlite3", recovery_interval_sec=60)
    root = tmp_path / "copied-frozen-release"
    directory = root / settings.release_id
    shutil.copytree(settings.release_root / settings.release_id, directory)
    if fault == "changed_snapshot_bytes":
        with (directory / "snapshot.json").open("ab") as target:
            target.write(b"\nexplicit-corruption")
    else:
        manifest_path = directory / "manifest.json"
        manifest = json.loads(manifest_path.read_bytes())
        manifest["knowledge_version"] = "explicit-mixed-wrong-version"
        payload = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
        manifest_path.write_bytes(payload)
    began = time.perf_counter_ns()
    broken = create_app(settings.model_copy(update={"release_root": root}))
    with pytest.raises(ValueError):
        with TestClient(broken):
            pass
    fault_ms = (time.perf_counter_ns() - began) / 1_000_000
    assert not broken.state.container.ready
    assert not broken.state.container.worker.is_alive
    began = time.perf_counter_ns()
    with TestClient(create_app(settings)) as client:
        assert client.get("/health/ready").status_code == 200
    record_timing(
        record_testsuite_property, fault, fault_ms, (time.perf_counter_ns() - began) / 1_000_000
    )


@pytest.mark.offline_neo4j
def test_actual_neo4j_stop_allows_cold_full_application_and_one_decimal_api(
    tmp_path, record_testsuite_property
):
    assert os.environ.get("SMART_COOKING_P6_OFFLINE_TEST") == "1", "需由真实停图监督器执行"
    with socket.socket() as probe:
        probe.settimeout(1)
        assert probe.connect_ex(("127.0.0.1", 7687)) != 0
    began = time.perf_counter_ns()
    with TestClient(create_app(AppSettings(database_path=tmp_path / "offline.sqlite3"))) as client:
        startup_ms = (time.perf_counter_ns() - began) / 1_000_000
        assert client.get("/health/ready").status_code == 200
        response, elapsed = request(client, "real-offline")
        assert response.status_code == 200, response.text
        assert response.json()["overview"]["timeSpent"].split(".")[-1].isdigit()
        assert len(response.json()["overview"]["timeSpent"].split(".")[-1]) == 1
        state = independent_persisted_scan(client.app, "real-offline")
        assert len(state.menu) == 1
    record_timing(
        record_testsuite_property, "neo4j_actual_stop", elapsed, startup_ms, bolt_port_closed=True
    )


def test_invalid_llm_and_stale_event_never_change_execution_facts(
    environment, record_testsuite_property
):
    app, client, _ = environment
    created = client.post(
        "/api/v1/sessions", json={"event_id": "create-language", "recipes": [STEAK]}
    )
    assert created.json()["status"] == "PUBLISHED", created.text
    sid = created.json()["session_id"]
    before = client.get(f"/api/v1/sessions/{sid}").json()
    provider = FixedProvider('{"action":"CHANGE_HEAT","duration_sec":0}')
    app.state.container.intents.provider = provider
    began = time.perf_counter_ns()
    invalid = client.post(
        f"/api/v1/sessions/{sid}/language-events",
        json={
            "event_id": "invalid-language",
            "text": "explicit synthetic illegal model output",
            "expected_state_revision": before["runtime"]["state_revision"],
            "base_plan_version": before["runtime"]["current_plan_version"],
        },
    )
    elapsed = (time.perf_counter_ns() - began) / 1_000_000
    assert invalid.status_code >= 400 and provider.calls == 1
    assert client.get(f"/api/v1/sessions/{sid}").json() == before
    stale = client.post(
        f"/api/v1/sessions/{sid}/events",
        json={
            "event_id": "stale-event",
            "event_type": "ADD_RECIPE",
            "expected_state_revision": 0,
            "base_plan_version": before["runtime"]["current_plan_version"],
            "payload": {"recipes": [STEAK]},
        },
    )
    assert stale.status_code == 409, stale.text
    assert client.get(f"/api/v1/sessions/{sid}").json() == before
    assert client.get("/health/ready").status_code == 200
    record_timing(
        record_testsuite_property,
        "invalid_llm_stale_event",
        elapsed,
        0,
        live_llm_calls=0,
        business_events_added=0,
    )


def test_sse_disconnect_and_restart_replay_do_not_complete_tasks(
    tmp_path, record_testsuite_property
):
    process = WindowsApiProcess(tmp_path / "real-tcp-stream")
    try:
        client = process.start()
        with closing(client):
            created = client.post(
                "/api/v1/sessions", json={"event_id": "create-stream", "recipes": [STEAK]}
            )
            assert created.json()["status"] == "PUBLISHED", created.text
            sid = created.json()["session_id"]
            url = f"/api/v1/sessions/{sid}/notifications"
            state = client.get(f"/api/v1/sessions/{sid}").json()
            batch = client.get(url).json()
            assert batch["messages"]
            began = time.perf_counter_ns()
            # 无限 SSE 响应读取第一条后关闭真实 TCP 流，服务器仍处于推送生命周期。
            with client.stream("GET", url + "/stream") as stream:
                assert stream.status_code == 200
                assert next(stream.iter_lines()).startswith("id:")
            elapsed = (time.perf_counter_ns() - began) / 1_000_000
            assert client.get(f"/api/v1/sessions/{sid}").json() == state
        process.stop()
        began = time.perf_counter_ns()
        with closing(process.start()) as restarted:
            assert restarted.get(url).json() == batch
            resumed = restarted.get(
                url + "/stream?once=true", headers={"Last-Event-ID": str(batch["cursor"])}
            )
            assert "data:" not in resumed.text
            assert restarted.get(f"/api/v1/sessions/{sid}").json() == state
        recovery_ms = (time.perf_counter_ns() - began) / 1_000_000
    finally:
        process.stop()
    record_timing(
        record_testsuite_property,
        "sse_disconnect_restart",
        elapsed,
        recovery_ms,
        execution_facts_unchanged=True,
        transport="REAL_TCP",
        server_restarts=1,
    )
