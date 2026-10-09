"""任务与请求结果在真实运行库中保留，重启不能补执行事实。"""

from fastapi.testclient import TestClient

from app.config import AppSettings
from app.main import create_app
from tests.contract.test_competition_endpoint import STEAK


def test_task_and_success_response_survive_restart(tmp_path):
    settings = AppSettings(database_path=tmp_path / "runtime.db")
    url = "/api/competition/plan?task_id=restart-meal"
    headers = {"Idempotency-Key": "restart-initial"}
    with TestClient(create_app(settings)) as client:
        first = client.post(url, json=[STEAK], headers=headers)
        assert first.status_code == 200, first.text
    with TestClient(create_app(settings)) as client:
        replay = client.post(url, json=[STEAK], headers=headers)
        assert replay.text == first.text
        assert client.post(url, json=[STEAK]).status_code == 409
        other = client.post("/api/competition/plan?task_id=other-meal", json=[STEAK])
        assert other.status_code == 200, other.text
        assert other.json()["overview"]["recipeCount"] == 1
