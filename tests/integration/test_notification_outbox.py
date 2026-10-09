"""通知与计划原子提交，重排撤销旧待发提醒并保留已发送历史。"""

from app.runtime.notifications import NotificationService
from app.storage.repositories import RuntimeRepository
from tests.integration.test_menu_events_replanning import service, start_event
from tests.runtime_support import event


def test_replan_cancels_pending_and_keeps_sent_records(tmp_path):
    runtime, planning, session = service(tmp_path)
    first = planning.apply_event(start_event(session))
    assert first.status == "PUBLISHED"
    notifications = NotificationService()
    with runtime.store.transaction() as tx:
        due = notifications.due(tx, "flow", runtime.clock.now())
        assert due and "task-" not in due[0].text
        sent = notifications.mark_sent(tx, due[0].notification_id, runtime.clock.now())
        assert notifications.mark_sent(tx, due[0].notification_id, runtime.clock.now()) == sent
    current = runtime.get("flow")
    change = event(
        current,
        "delay-notices",
        "DELAY_RECIPE",
        {
            "recipe_instance_id": current.menu[0].recipe_instance_id,
            "earliest_start_sec": 600,
        },
    )
    assert planning.apply_event(change).status == "PUBLISHED"
    with runtime.store.engine.connect() as tx:
        records = RuntimeRepository(tx).notification_records("flow")
    assert next(r for r in records if r.notification_id == sent.notification_id) == sent
    assert all(r.status in {"CANCELLED", "SENT"} for r in records if r.plan_version == 1)
    assert any(r.status == "PENDING" and r.plan_version == 2 for r in records)
    before = len(records)
    assert planning.apply_event(change).status == "NO_REPLAN"
    with runtime.store.engine.connect() as tx:
        assert len(RuntimeRepository(tx).notification_records("flow")) == before


def test_completed_operation_cancels_its_unsent_confirmation(tmp_path):
    runtime, planning, session = service(tmp_path)
    assert planning.apply_event(start_event(session)).status == "PUBLISHED"
    current = runtime.get("flow")
    binding = min(current.bindings, key=lambda b: b.assignment.interval.start_sec)
    task = binding.assignment.task_ids[0]
    payload = {"task_id": task, "execution_id": "notice-run"}
    assert (
        runtime.apply_event(event(current, "notice-start", "OPERATION_STARTED", payload)).status
        == "APPLIED"
    )
    current = runtime.get("flow")
    assert (
        runtime.apply_event(
            event(current, "notice-end", "OPERATION_COMPLETED", payload, at=180)
        ).status
        == "APPLIED"
    )
    with runtime.store.engine.connect() as tx:
        records = RuntimeRepository(tx).notification_records("flow")
    related = [r for r in records if task in r.task_ids]
    assert related
    assert all(r.status == "CANCELLED" for r in related)


def test_independent_simultaneous_carriers_receive_separate_reminders(tmp_path):
    runtime, planning, session = service(tmp_path)
    # 合成两道只有被动等待的菜，让真实调度器给出相同时段。
    runtime.knowledge = runtime.knowledge.model_copy(
        update={
            "recipes": tuple(
                recipe.model_copy(
                    update={"operations": (recipe.operations[1],), "dependencies": ()}
                )
                for recipe in runtime.knowledge.recipes
            )
        }
    )
    outcome = planning.apply_event(
        event(
            session,
            "parallel",
            "START_SESSION",
            {"recipes": [{"id": r.recipe_id, "name": r.name} for r in runtime.knowledge.recipes]},
        )
    )
    assert outcome.status == "PUBLISHED", outcome
    assignments = outcome.plan.validated.candidate.assignments
    assert len(assignments) == 2 and assignments[0].interval == assignments[1].interval
    with runtime.store.engine.connect() as tx:
        records = RuntimeRepository(tx).notification_records("flow")
    assert len(records) == 4
    assert all(len(r.task_ids) == 1 for r in records)


def test_failed_execution_cancels_its_pending_completion_reminder(tmp_path):
    runtime, planning, session = service(tmp_path)
    assert planning.apply_event(start_event(session)).status == "PUBLISHED"
    current = runtime.get("flow")
    task = min(current.bindings, key=lambda b: b.assignment.interval.start_sec).assignment.task_ids[
        0
    ]
    payload = {"task_id": task, "execution_id": "notice-failure"}
    assert (
        runtime.apply_event(event(current, "begin", "OPERATION_STARTED", payload)).status
        == "APPLIED"
    )
    current = runtime.get("flow")
    assert (
        runtime.apply_event(
            event(
                current,
                "failure",
                "OPERATION_FAILED",
                {**payload, "reason": "synthetic fault"},
                at=30,
            )
        ).status
        == "APPLIED"
    )
    with runtime.store.engine.connect() as tx:
        records = RuntimeRepository(tx).notification_records("flow")
    affected = [r for r in records if task in r.task_ids]
    assert affected and all(r.status == "CANCELLED" for r in affected)
