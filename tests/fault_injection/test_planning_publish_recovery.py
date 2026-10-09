"""服务边界保留落账事实，提交结果以 SQLite 持久化发布身份为准。"""

import pytest
from sqlalchemy import func, select

from app.runtime.notifications import NotificationService
from app.services.publishing import PlanPublisher
from app.storage import models
from tests.integration.test_menu_events_replanning import service, start_event


@pytest.mark.parametrize("exception", [TimeoutError, InterruptedError])
def test_service_returns_failure_after_publish_transaction_rolls_back(
    tmp_path, monkeypatch, exception
):
    runtime, planning, session = service(tmp_path)
    original = NotificationService.persist

    def interrupted(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise exception("synthetic:发布提交前中断")

    monkeypatch.setattr(NotificationService, "persist", interrupted)
    result = planning.apply_event(start_event(session))
    assert result.status == "FAILED", result
    assert result.event.status == "APPLIED"
    assert result.planning.failure.code == "NO_FEASIBLE_PLAN"
    assert {timing.stage for timing in result.planning.timings} >= {"COMPILATION", "PUBLICATION"}
    state = runtime.get("flow")
    assert len(state.menu) == 1 and state.runtime.state_revision == 1
    assert state.runtime.current_plan_version == 0 and state.dispatch_blocked
    assert state.requires_replan
    with runtime.store.engine.connect() as tx:
        assert tx.scalar(select(func.count()).select_from(models.plans)) == 0
        assert tx.scalar(select(func.count()).select_from(models.notifications)) == 0
    monkeypatch.setattr(NotificationService, "persist", original)
    restored = planning.drain("flow")
    assert restored.status == "PUBLISHED", restored
    assert restored.plan.plan_version == 1


def test_service_recovers_commit_after_publish_response_is_lost(tmp_path, monkeypatch):
    runtime, planning, session = service(tmp_path)
    original = PlanPublisher._publish

    def response_lost(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise ConnectionError("synthetic:提交后响应丢失")

    monkeypatch.setattr(PlanPublisher, "_publish", response_lost)
    started = start_event(session)
    result = planning.apply_event(started)
    assert result.status == "PUBLISHED", result
    assert result.event.status == "APPLIED"
    assert result.plan.plan_version == 1
    with runtime.store.engine.connect() as tx:
        before = (
            tx.scalar(select(func.count()).select_from(models.plans)),
            tx.scalar(select(func.count()).select_from(models.notifications)),
        )
    assert before[0] == 1 and before[1] > 0
    replay = planning.apply_event(started)
    assert replay.status == "NO_REPLAN" and replay.plan == result.plan
    with runtime.store.engine.connect() as tx:
        assert before == (
            tx.scalar(select(func.count()).select_from(models.plans)),
            tx.scalar(select(func.count()).select_from(models.notifications)),
        )
