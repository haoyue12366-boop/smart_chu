"""完成事件与通知同事务持久化；重复回执不重复提示。"""

import json

from sqlalchemy import select

from app.storage import models
from tests.integration.test_menu_events_replanning import service, start_event
from tests.runtime_support import event


def test_completion_notice_names_operation_and_survives_duplicate_event(tmp_path):
    runtime, planning, session = service(tmp_path)
    assert planning.apply_event(start_event(session)).status == "PUBLISHED"
    current = runtime.get("flow")
    binding = min(current.bindings, key=lambda b: b.assignment.interval.start_sec)
    payload = {"task_id": binding.assignment.task_ids[0], "execution_id": "completed-notice"}
    assert (
        runtime.apply_event(event(current, "begin", "OPERATION_STARTED", payload)).status
        == "APPLIED"
    )
    current = runtime.get("flow")
    completion = event(current, "end", "OPERATION_COMPLETED", payload, at=180)
    assert runtime.apply_event(completion).status == "APPLIED"
    assert runtime.apply_event(completion).status == "APPLIED"
    with runtime.store.engine.connect() as tx:
        messages = [
            json.loads(raw)
            for raw in tx.execute(select(models.notification_stream.c.body)).scalars()
        ]
    completed = [m for m in messages if m["kind"] == "OPERATION_COMPLETED"]
    assert len(completed) == 1
    notice = completed[0]
    assert current.menu[0].name in notice["text"]
    assert "已完成" in notice["text"]
    assert notice["data"]["task_ids"] == [t.root for t in binding.assignment.task_ids]
    assert notice["data"]["execution_id"] == "completed-notice"
    assert notice["data"]["source"] == current.runtime.execution_mode
    assert notice["data"]["completed_at"] == completion.occurred_at.isoformat()
    runtime.store.close()


def test_start_and_rejected_early_completion_do_not_emit_completed_notice(tmp_path):
    runtime, planning, session = service(tmp_path)
    assert planning.apply_event(start_event(session)).status == "PUBLISHED"
    current = runtime.get("flow")
    binding = next(b for b in current.bindings if b.carrier.duration_sec == 1200)
    payload = {"task_id": binding.assignment.task_ids[0], "execution_id": "not-completed"}
    # A completion without a matching start cannot produce a completion message.
    result = runtime.apply_event(event(current, "invalid-end", "OPERATION_COMPLETED", payload))
    assert result.status != "APPLIED"
    with runtime.store.engine.connect() as tx:
        messages = [
            json.loads(raw)
            for raw in tx.execute(select(models.notification_stream.c.body)).scalars()
        ]
    assert not any(m["kind"] == "OPERATION_COMPLETED" for m in messages)
    runtime.store.close()
