"""真实固定发布、Solver 与 SQLite；故障仅注入响应阶段，事实不得重复应用。"""

import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from app.api.competition_adapter import CompetitionAdapter
from app.config import AppSettings
from app.domain.candidates import stable_id
from app.domain.ids import EventId, SessionId
from app.main import create_app
from app.storage import models
from app.storage.competition_tasks import HttpRequestRepository
from tests.contract.test_competition_endpoint import STEAK


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    with TestClient(
        create_app(
            AppSettings(database_path=tmp_path_factory.mktemp("response-loss") / "runtime.db")
        )
    ) as value:
        yield value


def record_for(client, task, key):
    identity = stable_id("competition-http", task, key)
    with client.app.state.container.store.engine.connect() as tx:
        result = HttpRequestRepository(tx).get(identity)
    assert result is not None
    return result


def test_response_failure_after_publication_reuses_first_plan_without_second_recipe(
    client, monkeypatch
):
    task, key = "projection-crash", "original"
    original = CompetitionAdapter.to_response

    def fail_projection(*args, **kwargs):
        raise TimeoutError("测试注入：计划已提交，响应投影中断")

    monkeypatch.setattr(CompetitionAdapter, "to_response", fail_projection)
    url = f"/api/competition/plan?task_id={task}"
    headers = {"Idempotency-Key": key}
    failed = client.post(url, json=[STEAK], headers=headers)
    assert failed.status_code == 503, failed.text
    stored = record_for(client, task, key)
    assert stored.result.status == "PUBLISHED"
    assert stored.response_body is None
    monkeypatch.setattr(CompetitionAdapter, "to_response", original)
    replay = client.post(url, json=[STEAK], headers=headers)
    assert replay.status_code == 200, replay.text
    recovered = record_for(client, task, key)
    assert recovered.publication_id == stored.publication_id
    assert recovered.response_body == replay.text
    assert len(client.app.state.container.runtime.get(stored.session_id).menu) == 1


def test_invalid_projection_is_rejected_before_response_is_bound(client, monkeypatch):
    task, key = "projection-invalid", "original"
    original = CompetitionAdapter.to_response

    def wrong_projection(*args, **kwargs):
        response = original(*args, **kwargs)
        return response.model_copy(
            update={"overview": response.overview.model_copy(update={"timeSpent": "999999.0"})}
        )

    monkeypatch.setattr(CompetitionAdapter, "to_response", wrong_projection)
    failed = client.post(
        f"/api/competition/plan?task_id={task}", json=[STEAK], headers={"Idempotency-Key": key}
    )
    assert failed.status_code == 503, failed.text
    assert failed.json()["error"]["code"] == "STATE_INCOMPLETE"
    assert "overview" not in failed.json()
    assert record_for(client, task, key).response_body is None


def test_concurrent_identical_initial_request_creates_one_session_and_one_recipe(client):
    task, key = "concurrent-initial", "same-original"
    url = f"/api/competition/plan?task_id={task}"
    headers = {"Idempotency-Key": key}
    with ThreadPoolExecutor(max_workers=2) as pool:
        calls = [pool.submit(client.post, url, json=[STEAK], headers=headers) for _ in range(2)]
        results = [call.result(timeout=15) for call in calls]
    assert all(r.status_code in {200, 503} for r in results), [r.text for r in results]
    replay = client.post(url, json=[STEAK], headers=headers)
    assert replay.status_code == 200, replay.text
    stored = record_for(client, task, key)
    session = client.app.state.container.runtime.get(stored.session_id)
    assert len(session.menu) == 1
    assert session.runtime.state_revision == 1
    assert session.runtime.current_plan_version == 1
    assert stored.response_body == replay.text
    assert all(r.text == replay.text for r in results if r.status_code == 200)


def failed_receipt(client, task, key):
    response = client.post(
        f"/api/competition/plan?task_id={task}", json=[STEAK], headers={"Idempotency-Key": key}
    )
    assert response.status_code == 200, response.text
    stored = record_for(client, task, key)
    failed = stored.result.model_copy(update={"status": "FAILED", "plan": None})
    with client.app.state.container.store.transaction() as tx:
        tx.execute(
            update(models.http_requests)
            .where(models.http_requests.c.request_id == stored.request_id)
            .values(result_body=failed.model_dump_json(), publication_id=None, response_body=None)
        )
    return response, stored, failed


def test_failed_receipt_recovers_committed_publication_without_new_event_or_solver(
    client, monkeypatch
):
    task, key = "failed-receipt", "original"
    response, stored, _ = failed_receipt(client, task, key)
    runtime, planner = client.app.state.container.for_session(stored.session_id)
    before = runtime.get(stored.session_id)

    def prohibit_second_application(*args, **kwargs):
        pytest.fail("失败回执恢复只能读取已提交发布，不能重新应用事实或请求求解")

    monkeypatch.setattr(planner, "apply_event", prohibit_second_application)
    replay = client.post(
        f"/api/competition/plan?task_id={task}", json=[STEAK], headers={"Idempotency-Key": key}
    )
    assert replay.status_code == 200, replay.text
    assert replay.text == response.text
    assert runtime.get(stored.session_id) == before
    recovered = record_for(client, task, key)
    assert recovered.result.status == "PUBLISHED"
    assert recovered.publication_id == stored.publication_id


@pytest.mark.parametrize("fault", ["different_session", "different_event", "older_revision"])
def test_failed_receipt_cannot_bind_publication_from_other_identity(client, fault):
    task, key = "receipt-guard-" + fault, "original"
    _, stored, failed = failed_receipt(client, task, key)
    plan, event = stored.result.plan, stored.result.event
    assert plan is not None and event is not None
    if fault == "different_session":
        plan = plan.model_copy(update={"session_id": SessionId("other-session")})
    elif fault == "different_event":
        event = event.model_copy(update={"event_id": EventId("different-event")})
    else:
        plan = plan.model_copy(update={"state_revision": 0})
    changed = failed.model_copy(update={"status": "PUBLISHED", "event": event, "plan": plan})
    with client.app.state.container.store.transaction() as tx:
        HttpRequestRepository(tx).finish(stored.request_id, changed)
    assert record_for(client, task, key).result == failed


def test_duplicate_inflight_http_identity_returns_pending_without_second_execution(
    client, monkeypatch
):
    services = client.app.state.container
    original = services.planner.apply_event
    entered, release, counter_lock = Event(), Event(), Lock()
    calls = 0

    def pause_first(*args, **kwargs):
        nonlocal calls
        with counter_lock:
            calls += 1
            first = calls == 1
        if first:
            entered.set()
            assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(services.planner, "apply_event", pause_first)
    url = "/api/competition/plan?task_id=inflight-identity"
    headers = {"Idempotency-Key": "original"}
    with ThreadPoolExecutor(max_workers=1) as pool:
        initial = pool.submit(client.post, url, json=[STEAK], headers=headers)
        assert entered.wait(5)
        try:
            started = time.monotonic()
            duplicate = client.post(url, json=[STEAK], headers=headers)
            assert time.monotonic() - started < 0.5
            assert duplicate.status_code == 503, duplicate.text
            assert duplicate.json()["error"]["code"] == "PLANNING_PENDING"
            assert calls == 1
        finally:
            release.set()
            first = initial.result(timeout=10)
    assert first.status_code == 200, first.text
    assert client.post(url, json=[STEAK], headers=headers).text == first.text
