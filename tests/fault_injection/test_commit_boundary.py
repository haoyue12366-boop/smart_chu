"""SQLite 提交前中断与提交后响应丢失，不能产生半计划或重复发布。"""

from contextlib import contextmanager

import pytest
from sqlalchemy import func, select

from app.runtime.notifications import NotificationService
from app.storage import models
from tests.integration.test_plan_compare_and_swap import prepare


def counts(store):
    with store.engine.connect() as tx:
        return tuple(
            tx.scalar(select(func.count()).select_from(table))
            for table in (models.plans, models.notifications)
        )


def test_failure_before_commit_rolls_back_plan_notifications_and_session(tmp_path, monkeypatch):
    store, runtime, publisher, validated, context = prepare(tmp_path)
    original = NotificationService.persist

    def interrupted(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise InterruptedError("合成提交前中断")

    monkeypatch.setattr(NotificationService, "persist", interrupted)
    with pytest.raises(InterruptedError):
        publisher.publish(validated, context)
    assert counts(store) == (0, 0)
    assert runtime.get("publish").runtime.current_plan_version == 0
    assert runtime.get("publish").dispatch_blocked
    monkeypatch.setattr(NotificationService, "persist", original)
    assert publisher.publish(validated, context).plan_version == 1


def test_response_lost_after_commit_recovers_durable_publication_identity(tmp_path, monkeypatch):
    store, runtime, publisher, validated, context = prepare(tmp_path)
    original = store.transaction

    @contextmanager
    def response_lost():
        with original() as tx:
            yield tx
        raise ConnectionError("合成响应丢失，SQLite 已提交")

    monkeypatch.setattr(store, "transaction", response_lost)
    with pytest.raises(ConnectionError):
        publisher.publish(validated, context)
    committed_counts = counts(store)
    assert committed_counts[0] == 1 and committed_counts[1] > 0
    assert runtime.get("publish").runtime.current_plan_version == 1
    # 重试只读提交身份，不再次写库；故障仍注入也能恢复成功结果。
    recovered = publisher.publish(validated, context)
    assert recovered.publication_id == context.publication_id
    assert recovered.plan_version == 1
    assert counts(store) == committed_counts
