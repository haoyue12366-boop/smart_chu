"""模拟计划动作可撤销，人工模式只生成待确认信息。"""

import pytest

from app.runtime.clock import SimulationClock
from app.runtime.feedback import FeedbackAdapter
from app.runtime.service import RuntimeService
from app.runtime.simulator import Simulator
from app.scheduling.cp_sat import CpSatScheduler
from app.services.planning import PlanningService
from app.storage.unit_of_work import UnitOfWork
from tests.integration.test_menu_events_replanning import service, start_event
from tests.runtime_support import ORIGIN, event, policy, synthetic_knowledge


def test_business_clock_never_changes_solver_clock_or_moves_backwards():
    clock = SimulationClock(ORIGIN)
    before = clock.monotonic_ns()
    clock.advance(100_000)
    assert clock.now() == ORIGIN.replace(day=30, hour=13, minute=46, second=40)
    assert 0 <= clock.monotonic_ns() - before < 1_000_000_000
    with pytest.raises(ValueError):
        clock.advance(99_999)


def test_simulation_is_replayable_and_future_disturbance_is_private(tmp_path):
    traces = []
    for folder in ("first", "second"):
        runtime, planning, session = service(tmp_path / folder)
        assert planning.apply_event(start_event(session)).status == "PUBLISHED"
        simulator = Simulator(runtime, "flow", seed=73)
        simulator.inject(
            at=800,
            kind="ADD_RECIPE",
            payload={"recipes": [{"id": "synthetic-1", "name": "合成腌制1"}]},
        )
        assert len(runtime.get("flow").menu) == 1
        trace = simulator.advance(780)
        assert len(runtime.get("flow").menu) == 1
        running = [e for e in runtime.get("flow").runtime.executions if e.status == "RUNNING"]
        assert len(running) == 1
        assert running[0].started_at == ORIGIN.replace(minute=3)
        trace += simulator.advance(800)
        assert len(runtime.get("flow").menu) == 2
        assert runtime.get("flow").requires_replan
        traces.append(tuple(e.model_dump(mode="json") for e in trace))
    assert traces[0] == traces[1]


def test_replaced_plan_future_start_is_cancelled(tmp_path):
    runtime, planning, session = service(tmp_path)
    assert planning.apply_event(start_event(session)).status == "PUBLISHED"
    simulator = Simulator(runtime, "flow", seed=1)
    current = runtime.get("flow")
    delayed = planning.apply_event(
        event(
            current,
            "delay",
            "DELAY_RECIPE",
            {
                "recipe_instance_id": current.menu[0].recipe_instance_id,
                "earliest_start_sec": 600,
            },
        )
    )
    assert delayed.status == "PUBLISHED", delayed
    assert simulator.advance(599) == ()
    assert runtime.get("flow").runtime.executions == ()
    assert simulator.advance(600)[0].event_type == "OPERATION_STARTED"


def test_manual_overdue_never_completes_or_releases_human(tmp_path):
    knowledge = synthetic_knowledge()
    clock = SimulationClock(ORIGIN)
    store = UnitOfWork(tmp_path / "manual.sqlite")
    store.migrate()
    runtime = RuntimeService(store, knowledge, clock)
    session = runtime.create_session("manual", "MANUAL_CONFIRM", ORIGIN, policy())
    planning = PlanningService(runtime, CpSatScheduler())
    assert planning.apply_event(start_event(session)).status == "PUBLISHED"
    current = runtime.get("manual")
    first = min(current.bindings, key=lambda b: b.assignment.interval.start_sec)
    payload = {"task_id": first.assignment.task_ids[0], "execution_id": "manual-mix"}
    assert (
        runtime.apply_event(event(current, "start-mix", "OPERATION_STARTED", payload)).status
        == "APPLIED"
    )
    clock.advance(300)
    before = runtime.get("manual")
    reminders = FeedbackAdapter().overdue(before, clock.now())
    assert len(reminders) == 1
    assert "确认" in reminders[0].text
    assert runtime.get("manual") == before
    assert any(o.released_at is None for o in before.runtime.details.occupancies)
    with pytest.raises(ValueError, match="模拟"):
        Simulator(runtime, "manual")
    completed = FeedbackAdapter().event(
        before, "confirm", "OPERATION_COMPLETED", payload, clock.now()
    )
    assert runtime.apply_event(completed).status == "APPLIED"
    assert all(o.released_at for o in runtime.get("manual").runtime.details.occupancies)


def test_wait_completion_and_remaining_update_cannot_shorten_required_marinade(tmp_path):
    runtime, planning, session = service(tmp_path)
    assert planning.apply_event(start_event(session)).status == "PUBLISHED"
    simulator = Simulator(runtime, "flow")
    simulator.advance(180)
    current = runtime.get("flow")
    running = next(e for e in current.runtime.executions if e.status == "RUNNING")
    payload = {"task_id": running.started_task_ids[0], "execution_id": running.execution_id}
    early = runtime.apply_event(
        event(current, "early-wait", "OPERATION_COMPLETED", payload, at=600)
    )
    assert early.status == "REJECTED"
    assert runtime.get("flow").runtime.executions == current.runtime.executions
    shortened = runtime.apply_event(
        event(
            current, "shorten-wait", "DURATION_UPDATED", {**payload, "remaining_sec": 100}, at=780
        )
    )
    assert shortened.status == "REJECTED"
    simulator.advance(1380)
    completed = next(
        e for e in runtime.get("flow").runtime.executions if e.execution_id == running.execution_id
    )
    assert completed.finished_at == current.runtime.time_origin.at(1380)
