"""加菜命令同步服务端时钟；人工事实事件仍严格核对客户端版本。"""

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import events, sessions
from app.api.errors import install_error_handlers
from app.config import AppSettings
from app.services.container import ServiceContainer
from tests.integration.test_schedule_clock import clock_service


@pytest.fixture
def recipe_client(tmp_path):
    runtime, planner, _ = clock_service(tmp_path)
    services = ServiceContainer(AppSettings(release_id=runtime.knowledge.release.release_id))
    services.store = runtime.store
    services.clock = runtime.clock
    services.knowledge = runtime.knowledge
    services.policy = runtime.get("clock").policy
    services.runtimes[services.settings.release_id] = runtime
    services.planners[services.settings.release_id] = planner
    services.ready = True
    app = FastAPI()
    app.state.container = services
    install_error_handlers(app)
    app.include_router(sessions.router)
    app.include_router(events.router)

    @app.middleware("http")
    async def timestamp(request, call_next):
        request.state.started_ns = time.monotonic_ns()
        request.state.request_id = "synthetic-recipe-command"
        return await call_next(request)

    with TestClient(app) as client:
        yield client, runtime
    runtime.store.close()


def addition(identity="clock-add", version=1):
    return {
        "event_id": identity,
        "base_plan_version": version,
        "recipes": [{"id": "clock-1", "name": "合成时钟工艺1"}],
    }


def test_addition_after_clock_progress_publishes_once_and_keeps_started_work(recipe_client):
    client, runtime = recipe_client
    before = runtime.get("clock")
    runtime.clock.advance(60)
    result = client.post("/api/v1/sessions/clock/recipes", json=addition())
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "PUBLISHED", result.text
    state = runtime.get("clock")
    assert state.runtime.state_revision > before.runtime.state_revision
    assert state.runtime.current_plan_version == 2 and len(state.menu) == 2
    assert state.runtime.executions
    execution = state.runtime.executions[0]
    assert execution.started_at == before.runtime.time_origin.start_at
    assert execution.task_spans[0].interval.start_sec == 0
    assert result.json()["planning"]["serial_reference_validation"]["valid"]
    runtime.clock.advance(180)
    retry = client.post("/api/v1/sessions/clock/recipes", json=addition())
    assert retry.status_code == 200 and retry.json() == result.json()
    assert runtime.get("clock") == state


def test_old_plan_and_duplicate_recipe_do_not_modify_the_menu(recipe_client):
    client, runtime = recipe_client
    first = client.post("/api/v1/sessions/clock/recipes", json=addition())
    assert first.status_code == 200
    before = runtime.get("clock")
    stale = client.post("/api/v1/sessions/clock/recipes", json=addition("old-plan", 1))
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "STATE_CONFLICT"
    duplicate = client.post("/api/v1/sessions/clock/recipes", json=addition("duplicate", 2))
    assert duplicate.status_code == 409 and duplicate.json()["error"]["code"] == "DUPLICATE_RECIPE"
    assert runtime.get("clock") == before


def test_stale_fact_feedback_remains_rejected(recipe_client):
    client, runtime = recipe_client
    before = runtime.get("clock")
    runtime.clock.advance(60)
    response = client.post(
        "/api/v1/sessions/clock/events",
        json={
            "event_id": "stale-reset",
            "event_type": "RESET_SESSION",
            "expected_state_revision": before.runtime.state_revision,
            "base_plan_version": before.runtime.current_plan_version,
            "payload": {"reason": "合成旧版本反馈"},
        },
    )
    assert response.status_code == 409 and response.json()["status"] == "EVENT_REJECTED"
    assert runtime.get("clock").status == "ACTIVE"
