"""真实计划持久通知的断连、游标与重启；读消息不提交执行事实。"""

import json
from uuid import uuid4

from fastapi.testclient import TestClient

from app.config import ROOT, AppSettings
from app.main import create_app
from app.scheduling.worker import SolverWorker
from app.storage.repositories import RuntimeRepository
from tests.integration.test_running_thermal_batch import running_h02

STEAK = {"id": "61e6c51fec6e1d65587067e1", "name": "低温牛排"}


def test_stove_choice_notice_identifies_the_selected_physical_burner(tmp_path):
    with TestClient(create_app(AppSettings(database_path=tmp_path / "notice.db"))) as client:
        initial = client.post(
            "/api/v1/sessions",
            json={
                "event_id": str(uuid4()),
                "mode": "SIMULATED",
                "recipes": [STEAK],
            },
        )
        assert initial.json()["status"] == "PUBLISHED", initial.text
        sid = initial.json()["session_id"]
        services = client.app.state.container
        runtime, _ = services.for_session(sid)
        current = runtime.get(sid)
        binding = next(
            item
            for item in current.bindings
            if any(
                use.resource_type == "DEVICE" and use.physical_resource_id in {"stove_1", "stove_2"}
                for use in item.carrier.resource_uses
            )
        )
        resource = next(
            use for use in binding.carrier.resource_uses if use.resource_type == "DEVICE"
        )
        runtime.clock.advance(binding.assignment.interval.start_sec)
        before = client.get(f"/api/v1/sessions/{sid}").json()
        messages = client.get(f"/api/v1/sessions/{sid}/notifications").json()["messages"]
        notice = next(
            message
            for message in messages
            if message["kind"] == "START"
            and set(message["data"]["task_ids"]).intersection(
                t.root for t in binding.assignment.task_ids
            )
        )
        assert resource.physical_resource_id in notice["text"]
        assert "stove_choice" not in notice["text"]
        assert client.get(f"/api/v1/sessions/{sid}").json() == before


def test_disconnected_versions_and_cursor_survive_restart_without_execution(tmp_path):
    settings = AppSettings(database_path=tmp_path / "runtime.db")
    app = create_app(settings)
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/sessions",
            json={"event_id": str(uuid4()), "mode": "MANUAL_CONFIRM", "recipes": [STEAK]},
        )
        assert created.status_code == 200, created.text
        sid = created.json()["session_id"]
        state = client.get(f"/api/v1/sessions/{sid}").json()
        fixtures = json.loads(
            (ROOT / "data/preparations/p5-v1/api_scenarios.json").read_text(encoding="utf-8")
        )["fixtures"]
        added = client.post(
            f"/api/v1/sessions/{sid}/events",
            json={
                "event_id": str(uuid4()),
                "event_type": "ADD_RECIPE",
                "expected_state_revision": state["runtime"]["state_revision"],
                "base_plan_version": state["runtime"]["current_plan_version"],
                "payload": {"recipes": fixtures["add_fourth"]["body"]},
            },
        )
        assert added.status_code == 200, added.text
        assert added.json()["status"] == "PUBLISHED", added.text
        before_read = client.get(f"/api/v1/sessions/{sid}").json()
        url = f"/api/v1/sessions/{sid}/notifications"
        batch = client.get(url).json()
        changes = [m for m in batch["messages"] if m["kind"] == "PLAN_CHANGED"]
        assert [m["plan_version"] for m in changes] == [1, 2]
        assert len({m["event_id"] for m in batch["messages"]}) == len(batch["messages"])
        assert client.get(f"/api/v1/sessions/{sid}").json() == before_read
        replay = client.get(url).json()
        assert replay == batch
        response = client.get(
            url + "/stream?once=true", headers={"Last-Event-ID": str(batch["cursor"])}
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert "data:" not in response.text
        with app.state.container.store.engine.connect() as tx:
            notices = RuntimeRepository(tx).notification_records(sid)
        assert not any(n.plan_version == 1 and n.status == "PENDING" for n in notices)
    with TestClient(create_app(settings)) as client:
        assert client.get(url).json() == batch
        assert client.get(url, params={"after": batch["cursor"]}).json()["messages"] == []
        assert client.get(f"/api/v1/sessions/{sid}").json() == before_read


def test_actual_h02_member_end_notice_uses_selected_device_and_does_not_finish_execution(tmp_path):
    # 固定 P4 发布的真实两菜热批次；离线准备只用于保证目标批次实际被选择。
    with SolverWorker() as worker:
        runtime, _, _, binding, running, heats = running_h02(tmp_path, worker)
    runtime.store.engine.dispose()
    settings = AppSettings(database_path=tmp_path / "flow.sqlite", recovery_interval_sec=60)
    with TestClient(create_app(settings)) as client:
        services = client.app.state.container
        runtime, _ = services.for_session("flow")
        runtime.clock.advance(heats[0].interval.end_sec)
        before = client.get("/api/v1/sessions/flow").json()
        messages = client.get("/api/v1/sessions/flow/notifications").json()
        member_notice = next(
            message
            for message in messages["messages"]
            if message["kind"] == "EXPECTED_END"
            and set(message["data"]["task_ids"]) == {span.task_id.root for span in heats}
        )
        assert "请确认是否完成" in member_notice["text"]
        for item in before["menu"]:
            assert item["name"] in member_notice["text"]
        for use in binding.carrier.resource_uses:
            if use.resource_type == "DEVICE":
                assert use.physical_resource_id in member_notice["text"]
                for value in use.configuration:
                    assert f"{value.parameter}={value.value}" in member_notice["text"]
        assert client.get("/api/v1/sessions/flow").json() == before
        execution = next(
            record
            for record in before["runtime"]["executions"]
            if record["execution_id"] == running.execution_id.root
        )
        assert execution["status"] == "RUNNING"
        assert (
            client.get(
                "/api/v1/sessions/flow/notifications", params={"after": messages["cursor"]}
            ).json()["messages"]
            == []
        )
