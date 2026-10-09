"""图形 DTO 来自同一持久问题，完整日期、真实端口与冻结资源不在前端重算。"""

from uuid import uuid4

from fastapi.testclient import TestClient

from app.config import AppSettings
from app.main import create_app


def test_presentation_covers_all_published_tasks_and_has_exact_resource_seconds(tmp_path):
    with TestClient(create_app(AppSettings(database_path=tmp_path / "runtime.db"))) as client:
        initial = client.post(
            "/api/v1/sessions",
            json={
                "event_id": str(uuid4()),
                "recipes": [{"id": "61e6c51fec6e1d65587067e1", "name": "低温牛排"}],
            },
        )
        assert initial.status_code == 200, initial.text
        sid = initial.json()["session_id"]
        url = f"/api/v1/sessions/{sid}/plans/1"
        result = client.get(url)
        assert result.status_code == 200, result.text
        value = result.json()
        display = value["presentation"]
        assert display["time_origin"] == value["plan"]["time_origin"]
        scheduled = {r["task_id"] for r in display["operations"]}
        prepared = {t for r in display["advance_preparations"] for t in r["task_ids"]}
        assert not scheduled & prepared
        assert scheduled | prepared == {t["task_id"] for t in value["problem"]["logical_tasks"]}
        assert display["resources"]
        assert len({r["entry_id"] for r in display["resources"]}) == len(display["resources"])
        assert all(r["end_sec"] >= r["start_sec"] >= 0 for r in display["resources"])
        assert any(r["resource_id"] == "human_1" for r in display["resources"])
        assert client.get(url).json() == value
