"""实际占用来自执行账，不要求先提交一条设备状态事件。"""

from uuid import uuid4

from fastapi.testclient import TestClient

from app.config import AppSettings
from app.main import create_app


def test_actual_device_occupation_is_visible_before_fault_or_recovery(tmp_path):
    with TestClient(create_app(AppSettings(database_path=tmp_path / "runtime.db"))) as client:
        initial = client.post(
            "/api/v1/sessions",
            json={
                "event_id": str(uuid4()),
                "mode": "SIMULATED",
                "recipes": [{"id": "61e6c51fec6e1d65587067e1", "name": "低温牛排"}],
            },
        )
        assert initial.status_code == 200, initial.text
        sid = initial.json()["session_id"]
        state = client.get(f"/api/v1/sessions/{sid}").json()
        result = client.post(
            f"/api/v1/sessions/{sid}/events",
            json={
                "event_id": str(uuid4()),
                "event_type": "ADVANCE_SIMULATION",
                "source": "SIMULATED",
                "expected_state_revision": state["runtime"]["state_revision"],
                "base_plan_version": state["runtime"]["current_plan_version"],
                "payload": {"advance_sec": 1},
            },
        )
        assert result.status_code == 200, result.text
        observed = client.get(f"/api/v1/sessions/{sid}").json()
        assert any(
            o["resource"]["resource_type"] == "DEVICE" and o["released_at"] is None
            for o in observed["runtime"]["details"]["occupancies"]
        )
        occupied = [
            d for d in observed["device_presentation"] if d["occupancy_status"] == "OCCUPIED"
        ]
        assert occupied and all(d["active_execution_id"] for d in occupied)
        assert all(d["availability_status"] == "UNOBSERVED" for d in occupied)
        assert client.get(f"/api/v1/sessions/{sid}").json() == observed
