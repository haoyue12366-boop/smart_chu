"""显式模拟模式经真实发布和事件物料账执行；普通模式不会自动执行。"""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import AppSettings
from app.domain.candidates import stable_id
from app.domain.time import TimeOrigin
from app.main import create_app
from app.storage.competition_tasks import HttpRequestRepository

STEAK = {"id": "61e6c51fec6e1d65587067e1", "name": "低温牛排"}


@pytest.fixture(scope="module")
def environment(tmp_path_factory):
    root = tmp_path_factory.mktemp("simulation-http")
    app = create_app(AppSettings(database_path=root / "runtime.db", recovery_interval_sec=60))
    with TestClient(app) as client:
        yield client, app


def create(client, mode="SIMULATED"):
    result = client.post(
        "/api/v1/sessions",
        json={"event_id": str(uuid4()), "mode": mode, "recipes": [STEAK]},
    )
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "PUBLISHED", result.text
    return result.json()["session_id"]


def control(client, sid, advance):
    state = client.get(f"/api/v1/sessions/{sid}").json()["runtime"]
    return {
        "event_id": str(uuid4()),
        "event_type": "ADVANCE_SIMULATION",
        "source": "SIMULATED",
        "expected_state_revision": state["state_revision"],
        "base_plan_version": state["current_plan_version"],
        "payload": {"advance_sec": advance},
    }


def test_advance_emits_start_and_completion_and_replay_does_not_double_time(environment):
    client, _ = environment
    sid = create(client)
    url = f"/api/v1/sessions/{sid}/events"
    first = client.post(url, json=control(client, sid, 0))
    assert first.status_code == 200, first.text
    state = client.get(f"/api/v1/sessions/{sid}").json()
    assert state["runtime"]["executions"], "推进应派发已发布且已到时的动作"
    end = min(
        span["interval"]["end_sec"]
        for record in state["runtime"]["executions"]
        for span in record["task_spans"]
        if span["task_id"] in record["started_task_ids"]
    )
    body = control(client, sid, end)
    completed = client.post(url, json=body)
    assert completed.status_code == 200, completed.text
    after = client.get(f"/api/v1/sessions/{sid}").json()
    assert after["runtime"]["now_offset_sec"] == end
    assert any(e.get("completed_task_ids") for e in after["runtime"]["executions"])
    assert client.post(url, json=body).json() == completed.json()
    assert client.get(f"/api/v1/sessions/{sid}").json() == after


def test_interrupted_advance_resumes_same_target_and_preserves_applied_fact(
    environment, monkeypatch
):
    client, app = environment
    sid = create(client)
    runtime, _ = app.state.container.for_session(sid)
    original = runtime.apply_event
    interrupted = False

    def lose_response(event, deadline=None):
        nonlocal interrupted
        result = original(event, deadline)
        if event.event_type == "OPERATION_STARTED" and not interrupted:
            interrupted = True
            raise TimeoutError("显式合成：事实已提交后响应中断")
        return result

    monkeypatch.setattr(runtime, "apply_event", lose_response)
    body = control(client, sid, 60)
    url = f"/api/v1/sessions/{sid}/events"
    first = client.post(url, json=body)
    assert first.status_code == 503, first.text
    partial = client.get(f"/api/v1/sessions/{sid}").json()
    assert partial["runtime"]["executions"]
    with app.state.container.store.engine.connect() as tx:
        record = HttpRequestRepository(tx).get(stable_id("internal-event", body["event_id"]))
    assert record.control_phase == "VALIDATED"
    retry = client.post(url, json=body)
    assert retry.status_code == 200, retry.text
    after = client.get(f"/api/v1/sessions/{sid}").json()
    assert after["runtime"]["now_offset_sec"] == 60
    assert len({e["execution_id"] for e in after["runtime"]["executions"]}) == len(
        after["runtime"]["executions"]
    )
    assert client.post(url, json=body).json() == retry.json()
    assert client.get(f"/api/v1/sessions/{sid}").json() == after


def test_old_version_and_manual_mode_cannot_emit_simulated_facts(environment):
    client, _ = environment
    for mode in ("MANUAL_CONFIRM", "SIMULATED"):
        sid = create(client, mode)
        before = client.get(f"/api/v1/sessions/{sid}").json()
        body = control(client, sid, 60)
        if mode == "SIMULATED":
            body["expected_state_revision"] = 0
        result = client.post(f"/api/v1/sessions/{sid}/events", json=body)
        assert result.status_code >= 400, result.text
        assert client.get(f"/api/v1/sessions/{sid}").json() == before


def test_explicit_simulation_timestamp_replay_uses_original_target(environment):
    client, _ = environment
    sid = create(client)
    url = f"/api/v1/sessions/{sid}/events"
    origin = TimeOrigin.model_validate(
        client.get(f"/api/v1/sessions/{sid}").json()["runtime"]["time_origin"]
    )
    body = control(client, sid, 1)
    body["occurred_at"] = origin.at(1).isoformat()
    first = client.post(url, json=body)
    assert first.status_code == 200, first.text
    advanced = client.post(url, json=control(client, sid, 1))
    assert advanced.status_code == 200, advanced.text
    before = client.get(f"/api/v1/sessions/{sid}").json()
    assert before["runtime"]["now_offset_sec"] == 2
    retry = client.post(url, json=body)
    assert retry.status_code == 200, retry.text
    assert retry.json() == first.json()
    assert client.get(f"/api/v1/sessions/{sid}").json() == before
    conflict = client.post(url, json={**body, "occurred_at": origin.at(2).isoformat()})
    assert conflict.status_code == 409, conflict.text
