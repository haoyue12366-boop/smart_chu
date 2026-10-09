"""真实固定发布的五字段投影；独立检查拒绝结构合法但事实错误的响应。"""

import json
from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.competition_adapter import CompetitionAdapter
from app.config import AppSettings
from app.domain.competition_decimal import DecimalCompetitionResponse
from app.main import create_app
from app.storage.repositories import RuntimeRepository
from app.validation.competition_contract import validate_competition_projection


@pytest.fixture(scope="module")
def context(tmp_path_factory):
    with TestClient(
        create_app(AppSettings(database_path=tmp_path_factory.mktemp("projection") / "runtime.db"))
    ) as client:
        initial = client.post(
            "/api/v1/sessions",
            json={
                "event_id": str(uuid4()),
                "recipes": [{"id": "61e6c51fec6e1d65587067e1", "name": "低温牛排"}],
            },
        )
        assert initial.status_code == 200, initial.text
        services = client.app.state.container
        sid = initial.json()["session_id"]
        with services.store.engine.connect() as tx:
            repo = RuntimeRepository(tx)
            plan = repo.plan(sid, 1)
            problem = repo.problem(sid, 1)
        assert plan is not None
        knowledge = services.knowledge_for(sid)
        response = CompetitionAdapter().to_response(plan, problem, knowledge)
        yield response, plan, problem, knowledge


def test_real_projection_matches_published_seconds_and_resource_parameters(context):
    response, plan, problem, knowledge = context
    validate_competition_projection(response, plan, problem, knowledge, timezone="Asia/Shanghai")


@pytest.mark.parametrize(
    "fault",
    [
        "overview_time",
        "overview_save",
        "finish_clock",
        "recipe_clock",
        "recipe_duration",
        "missing_operation",
        "missing_timeline",
        "missing_parameters",
        "wrong_temperature",
        "wrong_mode",
        "wrong_device_duration",
        "different_identity_name",
        "ingredient_amount",
        "missing_ingredient",
        "summary_amount",
        "summary_name",
        "missing_summary",
        "timeline_temperature",
        "timeline_duration",
        "missing_timeline_parameters",
    ],
)
def test_structurally_valid_projection_tampering_is_rejected(context, fault):
    response, plan, problem, knowledge = context
    raw = deepcopy(response.model_dump(mode="json"))
    steps = raw["recipeDetail"][0]["cookingSteps"]
    heated = next(s for s in steps if s.get("cookingParameters") and "将食材放入" in s["describe"])
    if fault == "overview_time":
        raw["overview"]["timeSpent"] = "999999.0"
    elif fault == "overview_save":
        raw["overview"]["timeSave"] = "999999.0"
    elif fault == "finish_clock":
        raw["overview"]["finishTime"] = (
            "00:01" if raw["overview"]["finishTime"] != "00:01" else "00:02"
        )
    elif fault == "recipe_clock":
        raw["cookingTimeline"][0]["startTime"] = (
            "00:01" if raw["cookingTimeline"][0]["startTime"] != "00:01" else "00:02"
        )
    elif fault == "recipe_duration":
        raw["cookingTimeline"][0]["timeSpent"] = "999999.0"
    elif fault == "missing_operation":
        steps.pop(0)
    elif fault == "missing_timeline":
        raw["detailTimeline"].pop(0)
    elif fault == "missing_parameters":
        heated.pop("cookingParameters")
    elif fault == "wrong_temperature":
        heated["cookingParameters"]["temperature"] = "9999"
    elif fault == "wrong_mode":
        heated["cookingParameters"]["mode"] = "错误但非空的模式"
    elif fault == "wrong_device_duration":
        heated["cookingParameters"]["time"] = "0.0"
    elif fault == "different_identity_name":
        raw["cookingTimeline"][0]["name"] = "结构可匹配的错误名称"
        raw["recipeDetail"][0]["name"] = "结构可匹配的错误名称"
        for item in raw["detailTimeline"]:
            if item.get("recipeNames"):
                item["recipeNames"] = ["结构可匹配的错误名称"]
            for parameter in item.get("parameters") or []:
                parameter["recipeName"] = "结构可匹配的错误名称"
    elif fault == "ingredient_amount":
        raw["recipeDetail"][0]["majorIngredients"][0]["unit"] = "999999g"
    elif fault == "missing_ingredient":
        raw["recipeDetail"][0]["majorIngredients"].pop(0)
    elif fault == "summary_amount":
        raw["ingredientsSummary"][0]["list"][0]["unit"] = "999999g"
    elif fault == "summary_name":
        raw["ingredientsSummary"][0]["list"][0]["name"] = "不存在的原料"
    elif fault == "missing_summary":
        raw["ingredientsSummary"].pop(0)
    else:
        row = next(item for item in raw["detailTimeline"] if item.get("parameters"))
        if fault == "timeline_temperature":
            row["parameters"][0]["temperature"] = "9999"
        elif fault == "timeline_duration":
            row["parameters"][0]["time"] = 999999.0
        else:
            row.pop("parameters")
    parsed = DecimalCompetitionResponse.model_validate(raw)
    with pytest.raises(ValueError):
        validate_competition_projection(parsed, plan, problem, knowledge, timezone="Asia/Shanghai")


def test_same_name_two_ids_remain_separate_instances_in_real_competition(client_same_name):
    response = client_same_name
    assert response["overview"]["recipeCount"] == 2
    assert [d["name"] for d in response["recipeDetail"]] == ["麻辣对虾", "麻辣对虾"]
    assert (
        response["recipeDetail"][0]["cookingSteps"] != response["recipeDetail"][1]["cookingSteps"]
    )


@pytest.fixture(scope="module")
def client_same_name(tmp_path_factory):
    choices = [
        {"id": "58e70b129f6429675ec80601", "name": "麻辣对虾"},
        {"id": "5fe197255f8f38795ea6fe79", "name": "麻辣对虾"},
    ]
    with TestClient(
        create_app(AppSettings(database_path=tmp_path_factory.mktemp("same-name") / "runtime.db"))
    ) as client:
        result = client.post("/api/competition/plan?task_id=same-name", json=choices)
        assert result.status_code == 200, result.text
        yield json.loads(result.text)
