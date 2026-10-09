"""同任务三菜初排、两次单菜追加与重试使用真实 P4 主链。"""

import json

from fastapi.testclient import TestClient

from app.config import ROOT, AppSettings
from app.main import create_app
from app.runtime.clock import SimulationClock
from app.storage.competition_tasks import HttpRequestRepository
from app.storage.repositories import RuntimeRepository
from tests.runtime_support import ORIGIN


def fixtures():
    return json.loads(
        (ROOT / "data/preparations/p5-v1/api_scenarios.json").read_text(encoding="utf-8")
    )["fixtures"]


def test_initial_three_then_two_single_insertions_and_replay(tmp_path):
    cases = fixtures()
    app = create_app(AppSettings(database_path=tmp_path / "runtime.db", recovery_interval_sec=60))
    wall = SimulationClock(ORIGIN)
    app.state.container.clock = wall
    with TestClient(app) as client:
        url = "/api/competition/plan?task_id=meal-001"
        first = client.post(
            url, json=cases["initial_three"]["body"], headers={"Idempotency-Key": "initial"}
        )
        assert first.status_code == 200, first.text
        assert first.json()["overview"]["recipeCount"] == 3
        assert app.state.container.worker.is_alive, "已发布初排后常驻进程应保留给连续追加"
        with app.state.container.store.engine.connect() as tx:
            sid = HttpRequestRepository(tx).task_session("meal-001")
            initial = RuntimeRepository(tx).get(sid)
        for index, fixture in enumerate(("add_fourth", "add_fifth"), 4):
            added = client.post(
                url, json=cases[fixture]["body"], headers={"Idempotency-Key": fixture}
            )
            if added.status_code == 503 and added.json()["error"]["code"] == "PLANNING_PENDING":
                session = app.state.container.for_session(sid)[0].get(sid)
                assert session.schedule_clock.replan_not_before_sec is not None
                wall.advance(session.schedule_clock.replan_not_before_sec)
                app.state.container.recover_pending()
                added = client.post(
                    url, json=cases[fixture]["body"], headers={"Idempotency-Key": fixture}
                )
            assert added.status_code == 200, (
                f"第 {index} 道：{added.headers.get('Server-Timing')}; {added.text}"
            )
            assert added.json()["overview"]["recipeCount"] == index
            assert len(added.json()["recipeDetail"]) == index
        replay = client.post(
            url, json=cases["initial_three"]["body"], headers={"Idempotency-Key": "initial"}
        )
        assert replay.text == first.text
        current = client.get(f"/api/v1/sessions/{sid}").json()
        assert current["runtime"]["time_origin"] == initial.runtime.time_origin.model_dump(
            mode="json"
        )
        assert len(current["menu"]) == 5
        assert current["runtime"]["execution_mode"] == "SCHEDULE_CLOCK"
        assert current["runtime"]["executions"]
        assert all(e["source"] == "SCHEDULE_CLOCK" for e in current["runtime"]["executions"])
        duplicate = client.post(url, json=cases["add_fourth"]["body"])
        assert duplicate.status_code == 409
        changed_key = client.post(
            url, json=cases["add_fifth"]["body"], headers={"Idempotency-Key": "initial"}
        )
        assert changed_key.status_code == 409
        multiple = client.post(url, json=cases["initial_three"]["body"])
        assert multiple.status_code == 422
