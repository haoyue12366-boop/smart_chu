"""事件提交后使用真实编译、两种求解和独立校验进行条件发布。"""

from threading import Event, Thread

from app.runtime.clock import SimulationClock
from app.runtime.service import RuntimeService
from app.scheduling.cp_sat import CpSatScheduler
from app.services.planning import PlanningService
from app.storage.unit_of_work import UnitOfWork
from tests.runtime_support import ORIGIN, event, policy, synthetic_knowledge


def service(tmp_path, solver=None, knowledge=None):
    knowledge = knowledge or synthetic_knowledge()
    store = UnitOfWork(tmp_path / "flow.sqlite")
    store.migrate()
    runtime = RuntimeService(store, knowledge, SimulationClock(ORIGIN))
    session = runtime.create_session("flow", "SIMULATED", ORIGIN, policy())
    return runtime, PlanningService(runtime, solver or CpSatScheduler()), session


def start_event(session):
    return event(
        session, "start", "START_SESSION", {"recipes": [{"id": "synthetic-0", "name": "合成腌制0"}]}
    )


def test_start_and_zero_second_add_publish_complete_menu_once(tmp_path):
    runtime, planning, session = service(tmp_path)
    first_event = start_event(session)
    first = planning.apply_event(first_event)
    assert first.status == "PUBLISHED", first
    assert first.planning.total_human_objective_optimized
    assert planning.apply_event(first_event).status == "NO_REPLAN"
    session = runtime.get("flow")
    add = event(
        session, "add", "ADD_RECIPE", {"recipes": [{"id": "synthetic-1", "name": "合成腌制1"}]}
    )
    second = planning.apply_event(add)
    assert second.status == "PUBLISHED", second
    assert second.budget_ms == 2400
    assert second.plan.plan_version == 2
    assert len(second.plan.validated.candidate.recipe_completions) == 2
    assert runtime.get("flow").runtime.state_revision == 2
    assert not runtime.get("flow").dispatch_blocked


def test_event_commits_during_solve_and_latest_revision_is_coalesced(tmp_path):
    entered, release = Event(), Event()

    class PausingSolver:
        paused = False

        def solve(self, problem, hint, deadline, **kwargs):
            if not self.paused:
                self.paused = True
                entered.set()
                assert release.wait(2)
            return CpSatScheduler().solve(problem, hint, deadline, **kwargs)

    runtime, planning, session = service(tmp_path, PausingSolver())
    responses = []
    thread = Thread(target=lambda: responses.append(planning.apply_event(start_event(session))))
    thread.start()
    assert entered.wait(2)
    current = runtime.get("flow")
    added = planning.apply_event(
        event(
            current,
            "during",
            "ADD_RECIPE",
            {"recipes": [{"id": "synthetic-1", "name": "合成腌制1"}]},
        )
    )
    assert added.status == "PENDING"
    assert runtime.get("flow").runtime.state_revision == 2
    release.set()
    thread.join(8)
    assert not thread.is_alive()
    assert responses[0].status == "PUBLISHED", responses
    assert responses[0].attempts == 2
    assert responses[0].plan.state_revision == 2
    assert len(responses[0].plan.validated.candidate.recipe_completions) == 2


def test_failed_replan_preserves_event_and_blocks_old_dispatch(tmp_path):
    runtime, planning, session = service(tmp_path)
    assert planning.apply_event(start_event(session)).status == "PUBLISHED"
    current = runtime.get("flow")
    first = min(current.bindings, key=lambda b: b.assignment.interval.start_sec)
    task = first.assignment.task_ids[0]
    started = event(
        current, "started", "OPERATION_STARTED", {"task_id": task, "execution_id": "mix-run"}
    )
    outcome = planning.apply_event(started)
    assert outcome.status == "NO_REPLAN", outcome
    current = runtime.get("flow")
    result = planning.apply_event(
        event(
            current,
            "late-add",
            "ADD_RECIPE",
            {"recipes": [{"id": "synthetic-1", "name": "合成腌制1"}]},
            at=1000,
        )
    )
    assert result.status == "FAILED"
    after = runtime.get("flow")
    assert len(after.menu) == 2
    assert after.runtime.executions[0].status == "RUNNING"
    assert after.dispatch_blocked
    assert after.runtime.current_plan_version == 1
