"""真实服务的新任务采用已批准预算，重启后的旧任务保留绑定策略。"""

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.config import ROOT, AppSettings
from app.domain.policy import SchedulingPolicy
from app.main import create_app
from app.services.deployment_policy import scheduling_strategy_policy
from tests.contract.test_competition_endpoint import STEAK


def test_default_ft_service_preserves_policy_and_budget_of_restored_original_sessions(tmp_path):
    settings = AppSettings(database_path=tmp_path / "policy-activation.sqlite3")
    original = SchedulingPolicy.model_validate_json(settings.policy_path.read_bytes())
    approved = scheduling_strategy_policy(original, "FT_KITCHEN")
    old = SchedulingPolicy.model_validate_json(
        (ROOT / "data/policies/p6-quality-v1.json").read_bytes()
    )
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
        assert actual.search_strategy == "FT_KITCHEN"
        assert actual.initial_budget == original.initial_budget
        assert actual.replan_budget == original.replan_budget
        services.runtime.create_session("old-policy-session", "SIMULATED", datetime.now(UTC), old)
    with TestClient(create_app(settings)) as client:
        services = client.app.state.container
        assert services.policy == approved
        restored_old = services.for_session("old-policy-session")[0].get("old-policy-session")
        restored_new = services.for_session(new_session_id)[0].get(new_session_id)
        assert restored_old.policy == old and restored_old.policy.replan_budget.total_ms == 3000
        assert restored_new.policy == approved
