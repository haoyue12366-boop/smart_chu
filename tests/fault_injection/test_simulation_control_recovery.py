"""真实模拟事实和运行库；模拟控制已执行而回执未提交的重启边界。"""

import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.contracts import EventRequest
from app.config import AppSettings
from app.domain.candidates import stable_id
from app.domain.events import RuntimeEvent
from app.domain.ids import EventId
from app.domain.ports import Deadline
from app.main import create_app
from app.services.http_requests import admit_event, request_digest
from app.services.simulation_control import prepare_simulation
from app.storage.repositories import RuntimeRepository
from tests.contract.test_competition_endpoint import STEAK


@pytest.mark.parametrize("root_committed", [False, True])
def test_executed_control_restart_preserves_target_and_simulated_facts(tmp_path, root_committed):
    settings = AppSettings(database_path=tmp_path / "simulation.db", recovery_interval_sec=60)
    with TestClient(create_app(settings)) as client:
        initial = client.post(
            "/api/v1/sessions",
            json={"event_id": str(uuid4()), "mode": "SIMULATED", "recipes": [STEAK]},
        )
        assert initial.json()["status"] == "PUBLISHED", initial.text
        sid = initial.json()["session_id"]
        services = client.app.state.container
        runtime, _ = services.for_session(sid)
        session = runtime.get(sid)
        body = EventRequest(
            event_id="durable-advance",
            event_type="ADVANCE_SIMULATION",
            source="SIMULATED",
            expected_state_revision=session.runtime.state_revision,
            base_plan_version=session.runtime.current_plan_version,
            payload={"advance_sec": 1},
        )
        target = session.runtime.time_origin.at(1)
        event = RuntimeEvent(
            session_id=sid,
            **body.model_dump(exclude={"occurred_at"}),
            occurred_at=target,
            received_at=target,
        )
        record = admit_event(
            services,
            stable_id("internal-event", body.event_id),
            request_digest({"session_id": sid, **body.model_dump(mode="json")}),
            event,
            body.model_dump(mode="json"),
        )
        prepared = prepare_simulation(
            runtime, record, Deadline(expires_at_ns=time.monotonic_ns() + 2_400_000_000)
        )
        assert prepared.control_phase == "EXECUTED"
        if root_committed:
            assert runtime.apply_event(prepared.event).status == "APPLIED"
        current = runtime.get(sid)
        other = prepared.event.model_copy(
            update={
                "event_id": EventId("intervening-simulation-observation"),
                "expected_state_revision": current.runtime.state_revision,
                "base_plan_version": current.runtime.current_plan_version,
            }
        )
        assert runtime.apply_event(other).status == "APPLIED"
        before = runtime.get(sid)
        assert before.runtime.now_offset_sec == 1
        assert before.runtime.executions
    with TestClient(create_app(settings)) as client:
        retried = client.post(f"/api/v1/sessions/{sid}/events", json=body.model_dump(mode="json"))
        assert retried.status_code == 200, retried.text
        assert retried.json()["status"] != "EVENT_REJECTED", retried.text
        runtime, _ = client.app.state.container.for_session(sid)
        after = runtime.get(sid)
        assert after.runtime.now_offset_sec == 1
        assert after.runtime.executions == before.runtime.executions
        assert after.ledger == before.ledger
        assert after.runtime.state_revision == before.runtime.state_revision + (not root_committed)
        with client.app.state.container.store.engine.connect() as tx:
            receipt = RuntimeRepository(tx).event(body.event_id)
        assert receipt is not None and receipt[1].status == "APPLIED"
