"""真实服务的新任务采用已批准预算，重启后的旧任务保留绑定策略。"""

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.config import ROOT, AppSettings
from app.domain.policy import SchedulingPolicy
from app.main import create_app
from tests.contract.test_competition_endpoint import STEAK


def test_default_service_adopts_approved_quality_budget_and_preserves_old_sessions(tmp_path):
    approved = SchedulingPolicy.model_validate_json(
        (ROOT / "data/policies/p6-cook-continuous-v1.json").read_bytes()
    )
    old = SchedulingPolicy.model_validate_json(
        (ROOT / "data/policies/p6-quality-v1.json").read_bytes()
    )
    settings = AppSettings(database_path=tmp_path / "policy-activation.sqlite3")
    with TestClient(create_app(settings)) as client:
        services = client.app.state.container
        assert services.policy == approved
        created = client.post(
            "/api/v1/sessions",
            json={"event_id": "approved-policy-menu", "mode": "SIMULATED", "recipes": [STEAK]},
        )
        assert created.status_code == 200 and created.json()["status"] == "PUBLISHED"
        new_session_id = created.json()["session_id"]
        actual = services.for_session(new_session_id)[0].get(new_session_id).policy
        assert actual == approved
        assert actual.initial_budget.total_ms == 7000
        assert actual.replan_budget.total_ms == 5000
        services.runtime.create_session("old-policy-session", "SIMULATED", datetime.now(UTC), old)
    with TestClient(create_app(settings)) as client:
        services = client.app.state.container
        assert services.policy == approved
        restored_old = services.for_session("old-policy-session")[0].get("old-policy-session")
        restored_new = services.for_session(new_session_id)[0].get(new_session_id)
        assert restored_old.policy == old and restored_old.policy.replan_budget.total_ms == 3000
        assert restored_new.policy == approved
