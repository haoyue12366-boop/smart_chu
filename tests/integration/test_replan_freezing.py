"""已开始腌制必须从真实起点计时，不能因加菜重新等待。"""

import time

from app.domain.ports import Deadline
from app.runtime.replanning import prepare_replan
from app.scheduling.cp_sat import CpSatScheduler
from app.services.replanning import ReplanningService
from tests.integration.test_plan_compare_and_swap import prepare
from tests.runtime_support import event


def test_elapsed_marinade_kept_and_active_human_released(tmp_path):
    store, runtime, publisher, validated, context = prepare(tmp_path)
    publisher.publish(validated, context)
    session = runtime.get("publish")
    mix = min(session.bindings, key=lambda b: b.assignment.interval.start_sec)
    start = event(
        session,
        "mix-start",
        "OPERATION_STARTED",
        {"task_id": mix.assignment.task_ids[0], "execution_id": "mix"},
        0,
    )
    assert runtime.apply_event(start).status == "APPLIED"
    session = runtime.get("publish")
    completed = event(
        session,
        "mix-complete",
        "OPERATION_COMPLETED",
        {"task_id": mix.assignment.task_ids[0], "execution_id": "mix"},
        180,
    )
    assert runtime.apply_event(completed).status == "APPLIED"
    session = runtime.get("publish")
    assert all(o.released_at for o in session.runtime.details.occupancies)
    wait = next(b for b in session.bindings if b.carrier.duration_sec == 1200)
    assert (
        runtime.apply_event(
            event(
                session,
                "wait-start",
                "OPERATION_STARTED",
                {"task_id": wait.assignment.task_ids[0], "execution_id": "wait"},
                180,
            )
        ).status
        == "APPLIED"
    )
    session = runtime.get("publish")
    added = runtime.apply_event(
        event(
            session,
            "new-menu",
            "ADD_RECIPE",
            {"recipes": [{"id": "synthetic-1", "name": "合成腌制1"}]},
            780,
        )
    )
    assert added.requires_replan
    session = runtime.get("publish")
    request = prepare_replan(session, added, runtime.knowledge, session.policy)
    running = next(e for e in request.runtime.executions if e.status == "RUNNING")
    assert running.remaining_sec == 600
    assert running.task_spans[0].interval.start_sec == 180
    assert running.task_spans[0].interval.end_sec == 1380
    planner = ReplanningService(runtime.knowledge, CpSatScheduler(), validated.candidate)
    result = planner.compute(request, Deadline(expires_at_ns=time.monotonic_ns() + 2_400_000_000))
    assert result.status == "VALIDATED", result
    assert len(result.candidate.recipe_completions) == 2
    store.close()


def test_pending_replan_ages_remaining_observation_from_business_clock(tmp_path):
    from app.storage.repositories import RuntimeRepository
    from tests.integration.test_menu_events_replanning import service, start_event

    runtime, planning, session = service(tmp_path)
    assert planning.apply_event(start_event(session)).status == "PUBLISHED"
    current = runtime.get("flow")
    binding = min(current.bindings, key=lambda b: b.assignment.interval.start_sec)
    assert (
        runtime.apply_event(
            event(
                current,
                "clock-start",
                "OPERATION_STARTED",
                {
                    "task_id": binding.assignment.task_ids[0],
                    "execution_id": "aged-execution",
                },
            )
        ).status
        == "APPLIED"
    )
    current = runtime.get("flow")
    assert (
        runtime.apply_event(
            event(
                current,
                "clock-add",
                "ADD_RECIPE",
                {
                    "recipes": [{"id": "synthetic-1", "name": "合成腌制1"}],
                },
            )
        ).status
        == "APPLIED"
    )
    runtime.clock.advance(60)
    published = planning.drain("flow")
    assert published.status == "PUBLISHED", published
    with runtime.store.engine.connect() as tx:
        problem = RuntimeRepository(tx).problem("flow", published.plan.plan_version)
    assert problem.runtime.now_offset_sec == 60
    assert problem.fixed_executions[0].remaining_sec == 120
    assert published.plan.validated.candidate.metrics.actual_human_work_sec == 60
    assert runtime.get("flow").runtime.executions[0].remaining_sec == 180
