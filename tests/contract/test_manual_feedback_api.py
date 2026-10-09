"""人工反馈准备只读；显式确认后的普通事件才写入物料与执行事实。"""

from uuid import uuid4

from fastapi.testclient import TestClient

from app.config import AppSettings
from app.main import create_app

STEAK = {"id": "61e6c51fec6e1d65587067e1", "name": "低温牛排"}


def test_read_only_proposal_and_explicit_manual_start_are_idempotent(tmp_path):
    app = create_app(AppSettings(database_path=tmp_path / "runtime.db", recovery_interval_sec=60))
    with TestClient(app) as client:
        result = client.post(
            "/api/v1/sessions",
            json={"event_id": str(uuid4()), "recipes": [STEAK], "mode": "MANUAL_CONFIRM"},
        )
        assert result.status_code == 200, result.text
        sid = result.json()["session_id"]
        before = client.get(f"/api/v1/sessions/{sid}").json()
        binding = min(before["bindings"], key=lambda b: b["assignment"]["interval"]["start_sec"])
        task = min(binding["task_spans"], key=lambda s: s["interval"]["start_sec"])["task_id"]
        url = f"/api/v1/sessions/{sid}/operations/{task}/feedback"
        prepared = client.get(url)
        assert prepared.status_code == 200, prepared.text
        proposal = prepared.json()
        assert proposal["requires_confirmation"] is True
        assert proposal["source"] == "MANUAL_CONFIRM"
        assert proposal["payload"]["output_status"] == "UNKNOWN"
        assert proposal["payload"]["resource_release_status"] == "UNCONFIRMED"
        assert client.get(f"/api/v1/sessions/{sid}").json() == before
        body = {
            "event_id": str(uuid4()),
            "event_type": "OPERATION_STARTED",
            "source": "MANUAL_CONFIRM",
            "expected_state_revision": proposal["state_revision"],
            "base_plan_version": proposal["plan_version"],
            "payload": proposal["payload"],
        }
        response = client.post(f"/api/v1/sessions/{sid}/events", json=body)
        assert response.status_code == 200, response.text
        assert response.json()["event"]["status"] == "APPLIED", response.text
        after = client.get(f"/api/v1/sessions/{sid}").json()
        assert after["runtime"]["executions"]
        assert client.post(f"/api/v1/sessions/{sid}/events", json=body).json() == response.json()
        assert client.get(f"/api/v1/sessions/{sid}").json() == after
        completion = client.get(url, params={"completed": "true"})
        assert completion.status_code == 200, completion.text
        assert completion.json()["payload"]["output_status"] == "UNKNOWN"
        assert completion.json()["payload"]["resource_release_status"] == "UNCONFIRMED"
        assert client.get(f"/api/v1/sessions/{sid}").json() == after
