"""真实发布的五字段比赛结果与一位小数分钟。"""

import re

import pytest
from fastapi.testclient import TestClient

from app.config import AppSettings
from app.main import create_app

STEAK = {"id": "61e6c51fec6e1d65587067e1", "name": "低温牛排"}


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    with TestClient(
        create_app(AppSettings(database_path=tmp_path_factory.mktemp("competition") / "runtime.db"))
    ) as value:
        yield value


def test_direct_array_and_exact_five_top_level_fields(client):
    response = client.post(
        "/api/competition/plan", json=[STEAK], headers={"Idempotency-Key": "static-1"}
    )
    assert response.status_code == 200, response.text
    value = response.json()
    assert set(value) == {
        "overview",
        "cookingTimeline",
        "ingredientsSummary",
        "detailTimeline",
        "recipeDetail",
    }
    assert value["overview"]["recipeCount"] == 1
    assert re.fullmatch(r"\d+\.\d", value["overview"]["timeSpent"])
    for item in value["detailTimeline"]:
        assert re.fullmatch(r"\d+\.\d-\d+\.\d", item["timeInterval"])
        for parameter in item.get("parameters", []):
            assert parameter["time"] * 10 == round(parameter["time"] * 10)
    replay = client.post(
        "/api/competition/plan", json=[STEAK], headers={"Idempotency-Key": "static-1"}
    )
    assert replay.text == response.text


@pytest.mark.parametrize(
    "payload",
    [
        [],
        [{"id": 1001, "name": "示例ID"}],
        [{**STEAK, "name": "错误名字"}],
        [STEAK, STEAK],
        {"recipes": [STEAK]},
    ],
)
def test_invalid_input_never_returns_five_field_success(client, payload):
    result = client.post("/api/competition/plan", json=payload)
    assert result.status_code >= 400
    assert "overview" not in result.json()
