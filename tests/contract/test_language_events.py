"""模型为显式固定响应替身；业务事件仍走真实知识、求解与存储。"""

import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import ROOT, AppSettings
from app.domain.candidates import stable_id
from app.domain.time import TimeOrigin
from app.main import create_app

STEAK = {"id": "61e6c51fec6e1d65587067e1", "name": "低温牛排"}


class FixedProvider:
    def __init__(self, raw, *, timeout=False):
        self.raw, self.timeout, self.calls = raw, timeout, 0

    async def generate(self, prompt):
        self.calls += 1
        assert "不生成时间表或设备参数" in prompt
        if self.timeout:
            raise TimeoutError("显式合成的供应商超时")
        return self.raw


@pytest.fixture(scope="module")
def environment(tmp_path_factory):
    root = tmp_path_factory.mktemp("language-api")
    app = create_app(
        AppSettings(
            database_path=root / "runtime.db",
            language_enabled=True,
            language_archive_path=root / "intent-runs",
        )
    )
    with TestClient(app) as client:
        yield client, app


def session(client):
    created = client.post(
        "/api/v1/sessions",
        json={"event_id": str(uuid4()), "recipes": [STEAK], "mode": "MANUAL_CONFIRM"},
    )
    assert created.status_code == 200, created.text
    assert created.json()["status"] == "PUBLISHED", created.text
    sid = created.json()["session_id"]
    return sid, client.get(f"/api/v1/sessions/{sid}").json()


def request_body(state, text="请处理这条指令"):
    return {
        "event_id": str(uuid4()),
        "text": text,
        "expected_state_revision": state["runtime"]["state_revision"],
        "base_plan_version": state["runtime"]["current_plan_version"],
    }


@pytest.mark.parametrize("case", ["unknown", "invalid-json", "timeout", "ambiguous"])
def test_unusable_intent_never_writes_facts(environment, case):
    client, app = environment
    sid, before = session(client)
    raw = {
        "unknown": json.dumps({"action": "ADD_RECIPE", "recipe_id": "not-published"}),
        "invalid-json": "not JSON",
        "timeout": "",
        "ambiguous": json.dumps(
            {"action": "CLARIFY", "question": "希望几点开始？", "options": ["选择明确时间"]}
        ),
    }[case]
    provider = FixedProvider(raw, timeout=case == "timeout")
    app.state.container.intents.provider = provider
    body = request_body(before)
    result = client.post(f"/api/v1/sessions/{sid}/language-events", json=body)
    if case == "ambiguous":
        assert result.status_code == 200
        assert result.json()["event_accepted"] is False
        assert result.json()["status"] == "NEEDS_CLARIFICATION"
    else:
        assert result.status_code >= 400, result.text
    assert client.get(f"/api/v1/sessions/{sid}").json() == before
    assert provider.calls == 1
    archive = next(
        p
        for p in app.state.container.settings.language_archive_path.glob("*.json")
        if json.loads(p.read_text(encoding="utf-8"))["text"] == body["text"]
        and json.loads(p.read_text(encoding="utf-8"))["context"]["session_id"] == sid
    )
    record = json.loads(archive.read_text(encoding="utf-8"))
    assert "rendered_prompt" in record and "raw_response" in record
    outcome = record["business_outcome"]
    assert outcome["status"] == ("NEEDS_CLARIFICATION" if case == "ambiguous" else "HTTP_ERROR")
    assert outcome["event_id"] is None


def test_explicit_add_is_once_and_model_does_not_decide_schedule(environment):
    client, app = environment
    sid, before = session(client)
    fixtures = json.loads(
        (ROOT / "data/preparations/p5-v1/api_scenarios.json").read_text(encoding="utf-8")
    )["fixtures"]
    recipe = fixtures["add_fourth"]["body"][0]
    provider = FixedProvider(json.dumps({"action": "ADD_RECIPE", "recipe_id": recipe["id"]}))
    app.state.container.intents.provider = provider
    body = request_body(before, "加一道" + recipe["name"])
    url = f"/api/v1/sessions/{sid}/language-events"
    result = client.post(url, json=body)
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "PUBLISHED", result.text
    after = client.get(f"/api/v1/sessions/{sid}").json()
    assert len(after["menu"]) == 2
    assert client.post(url, json=body).json() == result.json()
    assert client.get(f"/api/v1/sessions/{sid}").json() == after
    assert provider.calls == 1
    archive = app.state.container.settings.language_archive_path / (
        stable_id("intent", sid, body["event_id"]) + ".json"
    )
    record = json.loads(archive.read_text(encoding="utf-8"))
    assert record["request_id"] == body["event_id"]
    assert record["business_outcome"]["status"] == "PUBLISHED"
    assert record["business_outcome"]["event_id"] == result.json()["event"]["event_id"]
    assert record["business_outcome"]["publication_id"] == result.json()["plan"]["publication_id"]
    changed = {**body, "text": "不同的指令"}
    assert client.post(url, json=changed).status_code == 409


def test_old_context_is_revalidated_without_event(environment):
    client, app = environment
    sid, before = session(client)
    instance = before["menu"][0]["recipe_instance_id"]
    app.state.container.intents.provider = FixedProvider(
        json.dumps({"action": "CANCEL_RECIPE", "recipe_instance_id": instance})
    )
    body = {**request_body(before), "expected_state_revision": 0}
    result = client.post(f"/api/v1/sessions/{sid}/language-events", json=body)
    assert result.status_code == 409, result.text
    assert client.get(f"/api/v1/sessions/{sid}").json() == before
    archive = app.state.container.settings.language_archive_path / (
        stable_id("intent", sid, body["event_id"]) + ".json"
    )
    record = json.loads(archive.read_text(encoding="utf-8"))
    assert record["status"] == "PARSED"
    assert record["business_outcome"] == {
        "status": "HTTP_ERROR",
        "event_id": None,
        "error_code": "STATE_CONFLICT",
    }


def test_disabled_language_keeps_structured_service_available(environment):
    client, app = environment
    sid, before = session(client)
    app.state.container.intents.enabled = False
    try:
        result = client.post(f"/api/v1/sessions/{sid}/language-events", json=request_body(before))
        assert result.status_code == 422
        assert result.json()["error"]["code"] == "LANGUAGE_DISABLED"
        assert client.get(f"/api/v1/sessions/{sid}").json() == before
        assert client.get("/health/ready").status_code == 200
    finally:
        app.state.container.intents.enabled = True


def test_unstarted_cancellation_preserves_history_and_replays_once(environment):
    client, app = environment
    sid, before = session(client)
    instance = before["menu"][0]["recipe_instance_id"]
    provider = FixedProvider(
        json.dumps({"action": "CANCEL_RECIPE", "recipe_instance_id": instance})
    )
    app.state.container.intents.provider = provider
    body = request_body(before, "取消还没开始的低温牛排")
    url = f"/api/v1/sessions/{sid}/language-events"
    old = client.get(f"/api/v1/sessions/{sid}/plans/1").json()
    first = client.post(url, json=body)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "PUBLISHED", first.text
    after = client.get(f"/api/v1/sessions/{sid}").json()
    assert after["runtime"]["details"]["cancelled_instance_ids"] == [instance]
    assert after["runtime"]["executions"] == []
    assert client.get(f"/api/v1/sessions/{sid}/plans/1").json() == old
    assert client.post(url, json=body).json() == first.json()
    assert client.get(f"/api/v1/sessions/{sid}").json() == after
    assert provider.calls == 1


def test_started_recipe_cannot_be_cancelled_by_language(environment):
    client, app = environment
    created = client.post(
        "/api/v1/sessions", json={"event_id": str(uuid4()), "mode": "SIMULATED", "recipes": [STEAK]}
    )
    assert created.json()["status"] == "PUBLISHED", created.text
    sid = created.json()["session_id"]
    state = client.get(f"/api/v1/sessions/{sid}").json()
    advance = client.post(
        f"/api/v1/sessions/{sid}/events",
        json={
            "event_id": str(uuid4()),
            "event_type": "ADVANCE_SIMULATION",
            "source": "SIMULATED",
            "expected_state_revision": state["runtime"]["state_revision"],
            "base_plan_version": state["runtime"]["current_plan_version"],
            "payload": {"advance_sec": 0},
        },
    )
    assert advance.status_code == 200, advance.text
    before = client.get(f"/api/v1/sessions/{sid}").json()
    assert before["runtime"]["executions"]
    app.state.container.intents.provider = FixedProvider(
        json.dumps(
            {
                "action": "CANCEL_RECIPE",
                "recipe_instance_id": before["menu"][0]["recipe_instance_id"],
            }
        )
    )
    rejected = client.post(f"/api/v1/sessions/{sid}/language-events", json=request_body(before))
    assert rejected.status_code == 422, rejected.text
    assert client.get(f"/api/v1/sessions/{sid}").json() == before


def test_explicit_offset_time_is_earliest_preference_without_deadline(environment):
    client, app = environment
    sid, before = session(client)
    origin = TimeOrigin.model_validate(before["runtime"]["time_origin"])
    provider = FixedProvider(
        json.dumps(
            {
                "action": "DELAY_RECIPE",
                "recipe_instance_id": before["menu"][0]["recipe_instance_id"],
                "earliest_start_at": origin.at(600).isoformat(),
            }
        )
    )
    app.state.container.intents.provider = provider
    result = client.post(f"/api/v1/sessions/{sid}/language-events", json=request_body(before))
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "PUBLISHED", result.text
    after = client.get(f"/api/v1/sessions/{sid}").json()
    assert after["runtime"]["details"]["earliest_starts"] == [
        [before["menu"][0]["recipe_instance_id"], 600]
    ]
    version = after["runtime"]["current_plan_version"]
    stored = client.get(f"/api/v1/sessions/{sid}/plans/{version}").json()
    assert all(task["earliest_start_sec"] >= 600 for task in stored["problem"]["logical_tasks"])
    # latest_end_sec 还承载 Compiler 的有限建模边界，不把它误认成用户硬截止。
    assert after["policy"] == before["policy"]
    assert all(operation["start_sec"] >= 600 for operation in stored["presentation"]["operations"])
    assert after["runtime"]["executions"] == []
