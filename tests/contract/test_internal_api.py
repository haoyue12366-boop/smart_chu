"""内部 API 的真实初排、版本、幂等和只读知识。"""

import pytest
from fastapi.testclient import TestClient

from app.config import AppSettings
from app.main import create_app

RECIPE = {"id": "61e6c51fec6e1d65587067e1", "name": "低温牛排"}


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    settings = AppSettings(database_path=tmp_path_factory.mktemp("internal-api") / "runtime.db")
    with TestClient(create_app(settings)) as value:
        yield value


def test_session_initial_plan_history_and_retry(client):
    body = {"recipes": [RECIPE], "event_id": "internal-initial-1", "mode": "MANUAL_CONFIRM"}
    first = client.post("/api/v1/sessions", json=body)
    assert first.status_code == 200, first.text
    result = first.json()
    assert result["status"] == "PUBLISHED", first.text
    sid = result["session_id"]
    state = client.get(f"/api/v1/sessions/{sid}").json()
    assert state["runtime"]["current_plan_version"] == 1
    history = client.get(f"/api/v1/sessions/{sid}/plans/1").json()
    assert history["plan"]["publication_id"] == result["plan"]["publication_id"]
    again = client.post("/api/v1/sessions", json=body)
    assert again.status_code == 200
    assert again.json()["plan"]["publication_id"] == result["plan"]["publication_id"]
    assert client.get(f"/api/v1/sessions/{sid}").json()["runtime"] == state["runtime"]


def test_knowledge_unknown_id_and_name_mismatch(client):
    assert client.get("/api/v1/recipes/not-a-recipe/graph").status_code == 404
    response = client.post(
        "/api/v1/sessions",
        json={
            "recipes": [{**RECIPE, "name": "错误名称"}],
            "event_id": "bad-name",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "RECIPE_NAME_MISMATCH"


def test_invalid_event_has_no_fact_effect(client):
    response = client.post(
        "/api/v1/sessions",
        json={"recipes": [RECIPE], "event_id": "internal-initial-2", "mode": "MANUAL_CONFIRM"},
    )
    assert response.json()["status"] == "PUBLISHED", response.text
    sid = response.json()["session_id"]
    before = client.get(f"/api/v1/sessions/{sid}").json()
    response = client.post(
        f"/api/v1/sessions/{sid}/events",
        json={
            "event_id": "bad-payload",
            "event_type": "DEVICE_RELEASE_CONFIRMED",
            "expected_state_revision": before["runtime"]["state_revision"],
            "base_plan_version": before["runtime"]["current_plan_version"],
            "payload": {"device_id": "oven_1"},
        },
    )
    assert response.status_code == 422
    assert client.get(f"/api/v1/sessions/{sid}").json() == before
