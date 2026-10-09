"""真实编译、独立校验和 SQLite 条件发布。"""

import time

from app.domain.ports import Deadline, PlanningRequest
from app.domain.schedule import PublishConflict, PublishContext, ValidatedSchedule
from app.runtime.clock import SimulationClock
from app.runtime.service import RuntimeService
from app.scheduling.cp_sat import CpSatScheduler
from app.services.planning_core import PlanningCore
from app.services.publishing import PlanPublisher
from app.storage.unit_of_work import UnitOfWork
from tests.runtime_support import ORIGIN, event, policy, synthetic_knowledge


def prepare(tmp_path):
    knowledge = synthetic_knowledge()
    clock = SimulationClock(ORIGIN)
    store = UnitOfWork(tmp_path / "publish.sqlite")
    store.migrate()
    runtime = RuntimeService(store, knowledge, clock)
    session = runtime.create_session("publish", "SIMULATED", ORIGIN, policy())
    runtime.apply_event(
        event(
            session,
            "start",
            "START_SESSION",
            {"recipes": [{"id": "synthetic-0", "name": "合成腌制0"}]},
        )
    )
    session = runtime.get("publish")
    core = PlanningCore(solver=CpSatScheduler())
    result = core.compute(
        PlanningRequest(request_id="first", menu=session.menu, policy=session.policy),
        knowledge,
        session.runtime,
        Deadline(expires_at_ns=time.monotonic_ns() + 4_200_000_000),
    )
    assert result.status == "VALIDATED", result
    context = PublishContext(
        session_id=session.runtime.session_id,
        base_state_revision=session.runtime.state_revision,
        base_plan_version=0,
        knowledge_version=knowledge.release.knowledge_version,
        snapshot_id=knowledge.release.snapshot_id,
        request_id="first",
        publication_id="publication-1",
    )
    validated = ValidatedSchedule(candidate=result.candidate, validation=result.validation)
    return (
        store,
        runtime,
        PlanPublisher(store, knowledge, clock, core.last_problem),
        validated,
        context,
    )


def test_publish_persists_once_and_rejects_old_revision(tmp_path):
    store, runtime, publisher, validated, context = prepare(tmp_path)
    published = publisher.publish(validated, context)
    assert not isinstance(published, PublishConflict)
    assert publisher.publish(validated, context) == published
    assert runtime.get("publish").runtime.current_plan_version == 1
    stale = publisher.publish(validated, context.model_copy(update={"publication_id": "different"}))
    assert isinstance(stale, PublishConflict)
    store.close()


def test_new_event_during_solve_prevents_publication(tmp_path):
    store, runtime, publisher, validated, context = prepare(tmp_path)
    current = runtime.get("publish")
    runtime.apply_event(
        event(
            current, "add", "ADD_RECIPE", {"recipes": [{"id": "synthetic-1", "name": "合成腌制1"}]}
        )
    )
    assert isinstance(publisher.publish(validated, context), PublishConflict)
    assert runtime.get("publish").runtime.current_plan_version == 0
    store.close()
