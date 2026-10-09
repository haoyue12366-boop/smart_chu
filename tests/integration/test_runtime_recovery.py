"""重启恢复绑定版本与已发生事实，不追补预计完成事件。"""

import pytest

from app.runtime.clock import SimulationClock
from app.runtime.recovery import restore_session
from app.storage.unit_of_work import UnitOfWork
from tests.integration.test_menu_events_replanning import service, start_event
from tests.runtime_support import ORIGIN, event


def test_restart_keeps_running_execution_without_automatic_completion(tmp_path):
    runtime, planning, session = service(tmp_path)
    assert planning.apply_event(start_event(session)).status == "PUBLISHED"
    current = runtime.get("flow")
    first = min(current.bindings, key=lambda b: b.assignment.interval.start_sec)
    assert (
        runtime.apply_event(
            event(
                current,
                "before-restart",
                "OPERATION_STARTED",
                {
                    "task_id": first.assignment.task_ids[0],
                    "execution_id": "survives-restart",
                },
            )
        ).status
        == "APPLIED"
    )
    before = runtime.get("flow")
    knowledge = runtime.knowledge
    path = runtime.store.path
    runtime.store.close()
    clock = SimulationClock(ORIGIN)
    clock.advance(300)
    seen = []

    def fixture_loader(ref):
        seen.append(ref)
        return knowledge

    recovered = restore_session(UnitOfWork(path), "flow", clock, fixture_loader)
    assert recovered.get("flow") == before
    assert seen == [knowledge.release]
    assert recovered.get("flow").runtime.executions[0].status == "RUNNING"


def test_missing_or_substituted_pinned_knowledge_is_refused(tmp_path):
    runtime, _, _ = service(tmp_path)

    def missing(ref):
        raise FileNotFoundError(ref.release_id)

    with pytest.raises(ValueError, match="固定知识"):
        restore_session(runtime.store, "flow", runtime.clock, missing)
    wrong = runtime.knowledge.model_copy(
        update={
            "release": runtime.knowledge.release.model_copy(update={"snapshot_id": "different"})
        }
    )
    with pytest.raises(ValueError, match="固定知识"):
        restore_session(runtime.store, "flow", runtime.clock, lambda ref: wrong)
    assert runtime.get("flow").runtime.state_revision == 0
