"""真实麻辣对虾范围原料保留整批声明，经真实发布、预约与反馈路径验证。"""

from uuid import uuid4

from fastapi.testclient import TestClient

from app.config import AppSettings
from app.main import create_app
from app.storage.repositories import RuntimeRepository


def test_range_raw_lot_is_reserved_and_consumed_as_batch_without_invented_grams(tmp_path):
    with TestClient(create_app(AppSettings(database_path=tmp_path / "range.db"))) as client:
        initial = client.post(
            "/api/v1/sessions",
            json={
                "event_id": str(uuid4()),
                "recipes": [{"id": "5fe197255f8f38795ea6fe79", "name": "麻辣对虾"}],
            },
        )
        assert initial.status_code == 200, initial.text
        assert initial.json()["status"] == "PUBLISHED", initial.text
        sid = initial.json()["session_id"]
        services = client.app.state.container
        runtime, _ = services.for_session(sid)
        session = runtime.get(sid)
        raw = next(
            lot for lot in session.runtime.details.lots if lot.quantity_kind == "RECIPE_BATCH"
        )
        assert raw.unit is None
        assert raw.produced.fraction() == raw.available.fraction() == raw.reserved.fraction() == 1
        with services.store.engine.connect() as tx:
            problem = RuntimeRepository(tx).problem(sid, session.runtime.current_plan_version)
        demand = next(d for d in problem.material_flow.demands if d.supply_id == raw.spec_id)
        draft = client.get(f"/api/v1/sessions/{sid}/operations/{demand.task_id.root}/feedback")
        assert draft.status_code == 200, draft.text
        body = draft.json()
        movement = next(m for m in body["payload"]["consumed"] if m["lot_id"] == raw.lot_id)
        assert movement.get("quantity") is None
        assert movement["batch_share"] == {"numerator": 1, "denominator": 1}
        started = client.post(
            f"/api/v1/sessions/{sid}/events",
            json={
                "event_id": str(uuid4()),
                "event_type": "OPERATION_STARTED",
                "expected_state_revision": session.runtime.state_revision,
                "base_plan_version": session.runtime.current_plan_version,
                "payload": body["payload"],
            },
        )
        assert started.status_code == 200, started.text
        after = runtime.get(sid)
        lot = next(lot for lot in after.runtime.details.lots if lot.lot_id == raw.lot_id)
        assert lot.available.fraction() == lot.reserved.fraction() == 0
        assert lot.unit is None
