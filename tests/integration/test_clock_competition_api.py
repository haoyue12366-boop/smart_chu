"""真实HTTP、时钟、求解器和持久通知贯通；测试推进时钟而非等待厨房时长。"""

import pytest
from fastapi.testclient import TestClient

from app.config import AppSettings
from app.main import create_app
from app.runtime.clock import SimulationClock
from app.storage.repositories import RuntimeRepository
from tests.runtime_support import ORIGIN

STEAK = {"id": "61e6c51fec6e1d65587067e1", "name": "低温牛排"}
TOFU = {"id": "65433d6a45256c3ad7c270ab", "name": "手工鸡蛋豆腐"}


def test_insert_during_steaming_keeps_entire_past_and_new_tasks_after_insertion(tmp_path):
    settings = AppSettings(
        database_path=tmp_path / "insert-steaming.sqlite", recovery_interval_sec=60
    )
    app = create_app(settings)
    clock = SimulationClock(ORIGIN)
    app.state.container.clock = clock
    with TestClient(app) as client:
        url = "/api/competition/plan?task_id=steaming-insert"
        first = client.post(url, json=[TOFU], headers={"Idempotency-Key": "initial"})
        assert first.status_code == 200, first.text
        sid = client.get("/api/v1/competition-tasks/steaming-insert").json()["session_id"]
        services = app.state.container
        runtime, _ = services.for_session(sid)
        session = runtime.get(sid)
        with services.store.engine.connect() as tx:
            original = RuntimeRepository(tx).problem(sid, 1)
        steam_tasks = {
            task.task_id
            for task in original.logical_tasks
            if task.operation.action == "HEAT"
            and any(
                use.physical_resource_id == "steam_oven_1"
                for use in task.operation.resource_requirements
            )
        }
        assert steam_tasks, "真实菜谱必须含蒸制工序，不能用人工操作替代本用例"
        steaming = next(
            span.interval
            for binding in session.bindings
            for span in binding.task_spans
            if span.task_id in steam_tasks
        )
        inserted_at = (steaming.start_sec + steaming.end_sec) // 2
        clock.advance(inserted_at)
        before = services.advance_clock(sid)
        completed_before = {
            e.execution_id: e for e in before.runtime.executions if e.status == "COMPLETED"
        }
        active_before = {
            e.execution_id: e for e in before.runtime.executions if e.status == "RUNNING"
        }
        assert completed_before and active_before
        assert any(set(e.started_task_ids) & steam_tasks for e in active_before.values())
        initial_history = client.get(f"/api/v1/sessions/{sid}/plans/1").text
        added = client.post(url, json=[STEAK], headers={"Idempotency-Key": "addition"})
        assert added.status_code == 200, added.text
        assert added.json()["overview"]["recipeCount"] == 2
        after = runtime.get(sid)
        assert after.runtime.current_plan_version == 2
        assert after.runtime.executions == before.runtime.executions
        assert after.schedule_clock.started_at == before.schedule_clock.started_at
        with services.store.engine.connect() as tx:
            repo = RuntimeRepository(tx)
            replanned = repo.problem(sid, 2)
            plan = repo.plan(sid, 2)
        instance = next(r.recipe_instance_id for r in after.menu if r.recipe_id.root == STEAK["id"])
        new_tasks = {t.task_id for t in replanned.logical_tasks if t.recipe_instance_id == instance}
        assert new_tasks
        new_assignments = [
            a for a in plan.validated.candidate.assignments if set(a.task_ids) & new_tasks
        ]
        assert new_assignments
        assert all(a.interval.start_sec >= inserted_at for a in new_assignments)
        assert all(
            a.interval.start_sec >= inserted_at for a in plan.validated.candidate.assignments
        )
        assert min(a.interval.start_sec for a in new_assignments) < steaming.end_sec
        fixed = {e.execution_id: e for e in replanned.fixed_executions}
        assert all(fixed[identity] == execution for identity, execution in completed_before.items())
        for identity, execution in active_before.items():
            frozen = fixed[identity]
            assert frozen.status == "RUNNING"
            assert frozen.started_at == execution.started_at
            assert frozen.task_spans == execution.task_spans
            assert frozen.scheduled_resource_spans == execution.scheduled_resource_spans
        assert client.get(f"/api/v1/sessions/{sid}/plans/1").text == initial_history
        assert (
            client.post(url, json=[STEAK], headers={"Idempotency-Key": "addition"}).text
            == added.text
        )


@pytest.mark.parametrize("replan_endpoint", ["competition", "internal"])
def test_clock_competition_replans_active_step_and_streams_later_completion(
    tmp_path, replan_endpoint
):
    settings = AppSettings(database_path=tmp_path / "clock-api.sqlite", recovery_interval_sec=60)
    app = create_app(settings)
    clock = SimulationClock(ORIGIN)
    app.state.container.clock = clock
    with TestClient(app) as client:
        response = client.post(
            "/api/competition/plan?task_id=clock-api",
            json=[STEAK],
            headers={"Idempotency-Key": "initial"},
        )
        assert response.status_code == 200, response.text
        assert set(response.json()) == {
            "overview",
            "cookingTimeline",
            "ingredientsSummary",
            "detailTimeline",
            "recipeDetail",
        }
        sid = client.get("/api/v1/competition-tasks/clock-api").json()["session_id"]
        replan_url = (
            "/api/competition/replan?task_id=clock-api"
            if replan_endpoint == "competition"
            else f"/api/v1/sessions/{sid}/replan"
        )
        services = app.state.container
        runtime, _ = services.for_session(sid)
        session = runtime.get(sid)
        assert session.runtime.execution_mode == "SCHEDULE_CLOCK"
        first = min(session.bindings, key=lambda binding: binding.assignment.interval.start_sec)
        midpoint = (first.assignment.interval.start_sec + first.assignment.interval.end_sec) // 2
        clock.advance(midpoint)
        services.advance_clock(sid)
        running = runtime.get(sid)
        active = [
            execution for execution in running.runtime.executions if execution.status == "RUNNING"
        ]
        assert active
        end = max(span.interval.end_sec for execution in active for span in execution.task_spans)
        completed = client.post(
            replan_url,
            json={},
            headers={"Idempotency-Key": "replan"},
        )
        assert completed.status_code == 200, completed.text
        progress = client.get(f"/api/v1/sessions/{sid}").json()["clock_progress"]
        assert not progress["waiting_for_boundary"]
        assert runtime.get(sid).runtime.current_plan_version == 2
        assert runtime.get(sid).runtime.executions == running.runtime.executions
        assert client.get("/api/competition/notifications?task_id=clock-api").status_code == 200
        assert completed.json()["status"] == "PUBLISHED"
        assert completed.json()["plan"]["plan_version"] == 2
        with services.store.engine.connect() as tx:
            problem = RuntimeRepository(tx).problem(sid, 2)
        assignments = completed.json()["plan"]["validated"]["candidate"]["assignments"]
        assert all(assignment["interval"]["start_sec"] >= midpoint for assignment in assignments)
        assert problem.fixed_executions
        assert {e.execution_id for e in problem.fixed_executions} >= {
            e.execution_id for e in active
        }
        fixed = {execution.execution_id: execution for execution in problem.fixed_executions}
        for execution in active:
            assert fixed[execution.execution_id].status == "RUNNING"
            assert fixed[execution.execution_id].started_at == execution.started_at
            assert fixed[execution.execution_id].task_spans == execution.task_spans
        clock.advance(end)
        services.advance_clock(sid)
        messages = client.get("/api/competition/notifications?task_id=clock-api").json()
        notices = [item for item in messages["messages"] if item["kind"] == "OPERATION_COMPLETED"]
        assert notices and "低温牛排" in notices[0]["text"]
        assert "计划时钟推算" in notices[0]["text"]
        assert notices[0]["data"]["source"] == "SCHEDULE_CLOCK"
        cursor = messages["cursor"]
        stream = client.get(
            "/api/competition/notifications/stream?task_id=clock-api&once=true",
            headers={"Last-Event-ID": str(cursor)},
        )
        assert stream.status_code == 200 and "data:" not in stream.text
    restarted = create_app(settings)
    restarted.state.container.clock = clock
    with TestClient(restarted) as client:
        replay = client.get("/api/competition/notifications?task_id=clock-api").json()
        old = [m for m in replay["messages"] if m["kind"] == "OPERATION_COMPLETED"]
        assert old == notices
        assert (
            client.get(
                "/api/competition/notifications", params={"task_id": "clock-api", "after": cursor}
            ).json()["messages"]
            == []
        )
