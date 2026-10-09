"""合成腌制流程：重复菜单事件只发生一次，冲突不改变事实。"""

from app.runtime.clock import SimulationClock
from app.runtime.service import RuntimeService
from app.storage.unit_of_work import UnitOfWork
from tests.runtime_support import ORIGIN, event, policy, synthetic_knowledge


def test_late_failure_keeps_completion_and_advances_conflict_revision(tmp_path):
    from tests.integration.test_menu_events_replanning import service, start_event

    runtime, planning, session = service(tmp_path)
    assert planning.apply_event(start_event(session)).status == "PUBLISHED"
    session = runtime.get("flow")
    first = min(session.bindings, key=lambda b: b.assignment.interval.start_sec)
    payload = {"task_id": first.assignment.task_ids[0], "execution_id": "mix-done"}
    assert (
        runtime.apply_event(event(session, "begin", "OPERATION_STARTED", payload)).status
        == "APPLIED"
    )
    session = runtime.get("flow")
    assert (
        runtime.apply_event(event(session, "end", "OPERATION_COMPLETED", payload, at=180)).status
        == "APPLIED"
    )
    completed = runtime.get("flow")
    late = event(
        completed, "late-failure", "OPERATION_FAILED", {**payload, "reason": "合成迟到故障"}, at=181
    )
    outcome = runtime.apply_event(late)
    after = runtime.get("flow")
    assert outcome.status == "CONFLICT"
    assert after.runtime.executions == completed.runtime.executions
    assert after.runtime.state_revision == completed.runtime.state_revision + 1
    assert after.dispatch_blocked
    assert first.assignment.task_ids[0] in after.runtime.details.blocked_task_ids
    assert runtime.apply_event(late) == outcome


def test_duplicate_event_before_version_check_and_changed_payload_conflicts(tmp_path):
    store = UnitOfWork(tmp_path / "events.sqlite")
    store.migrate()
    service = RuntimeService(store, synthetic_knowledge(), SimulationClock(ORIGIN))
    state = service.create_session("test", "SIMULATED", ORIGIN, policy())
    started = event(
        state, "start", "START_SESSION", {"recipes": [{"id": "synthetic-0", "name": "合成腌制0"}]}
    )
    first = service.apply_event(started)
    assert first.status == "APPLIED" and first.requires_replan
    assert service.apply_event(started) == first
    conflict = started.model_copy(
        update={"payload": started.payload.model_copy(update={"earliest_start_sec": 1})}
    )
    assert service.apply_event(conflict).status == "CONFLICT"
    current = service.get("test")
    assert current.runtime.state_revision == 1 and len(current.menu) == 1
    store.close()
