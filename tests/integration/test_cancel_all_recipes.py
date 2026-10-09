"""取消全部需求发布可核对的空计划；已发生执行及未释放占用仍保留。"""

import time

import pytest

from app.compiler.compiler import ProblemCompiler
from app.domain.ports import Deadline
from app.domain.schedule import PublishContext, ValidatedSchedule
from app.domain.scheduling_problem import SchedulingProblem
from app.runtime.replanning import prepare_replan
from app.runtime.simulator import Simulator
from app.scheduling.greedy import GreedyScheduler
from app.services.publishing import PlanPublisher
from app.storage.repositories import RuntimeRepository
from app.validation.schedule import ScheduleValidator
from tests.integration.test_menu_events_replanning import service, start_event
from tests.runtime_support import event


def cancel_all(runtime, planning, at=0):
    session = runtime.get("flow")
    result = planning.apply_event(
        event(
            session,
            "cancel-last",
            "CANCEL_RECIPE",
            {"recipe_instance_id": session.menu[0].recipe_instance_id},
            at=at,
        )
    )
    assert result.event.status == "APPLIED" and result.status == "PUBLISHED", result
    assert result.plan.validated.validation.valid
    assert result.plan.validated.candidate.assignments == ()
    assert result.plan.validated.candidate.recipe_completions == ()
    assert result.plan.validated.candidate.metrics.remaining_makespan_sec == 0
    return result


def test_cancel_last_pending_recipe_publishes_empty_plan_and_allows_later_add(tmp_path):
    runtime, planning, session = service(tmp_path)
    assert planning.apply_event(start_event(session)).status == "PUBLISHED"
    result = cancel_all(runtime, planning)
    after = runtime.get("flow")
    assert result.plan.plan_version == 2 and not after.dispatch_blocked
    assert after.runtime.details.cancelled_instance_ids == (after.menu[0].recipe_instance_id.root,)
    with runtime.store.engine.connect() as tx:
        assert all(
            item.status == "CANCELLED"
            for item in RuntimeRepository(tx).notification_records("flow")
        )
    assert Simulator(runtime, "flow").advance(2000) == ()
    after = runtime.get("flow")
    assert not after.runtime.executions
    added = planning.apply_event(
        event(
            after,
            "new-order",
            "ADD_RECIPE",
            {"recipes": [{"id": "synthetic-1", "name": "合成腌制1"}]},
            at=2000,
        )
    )
    assert added.status == "PUBLISHED", added
    assert len(added.plan.validated.candidate.recipe_completions) == 1


def test_cancel_last_running_recipe_preserves_occupancy_until_actual_completion(tmp_path):
    runtime, planning, session = service(tmp_path)
    assert planning.apply_event(start_event(session)).status == "PUBLISHED"
    simulator = Simulator(runtime, "flow")
    simulator.advance(30)
    before = runtime.get("flow")
    record = before.runtime.executions[0]
    assert record.status == "RUNNING"
    assert any(item.released_at is None for item in before.runtime.details.occupancies)
    result = cancel_all(runtime, planning, at=30)
    assert result.plan.validated.candidate.metrics.actual_human_work_sec == 30
    assert result.plan.validated.candidate.metrics.total_human_work_sec == 180
    after = runtime.get("flow")
    assert after.runtime.executions == before.runtime.executions
    assert after.runtime.details.occupancies == before.runtime.details.occupancies
    simulator.advance(2000)
    after = runtime.get("flow")
    assert len(after.runtime.executions) == 1
    assert after.runtime.executions[0].status == "COMPLETED"
    assert after.runtime.executions[0].execution_id == record.execution_id
    assert all(item.released_at is not None for item in after.runtime.details.occupancies)


def test_publisher_rejects_empty_menu_when_only_one_of_two_recipes_was_cancelled(tmp_path):
    runtime, planning, session = service(tmp_path)
    initial = event(
        session,
        "two",
        "START_SESSION",
        {
            "recipes": [
                {"id": recipe.recipe_id, "name": recipe.name}
                for recipe in runtime.knowledge.recipes
            ]
        },
    )
    assert planning.apply_event(initial).status == "PUBLISHED"
    current = runtime.get("flow")
    assert (
        runtime.apply_event(
            event(
                current,
                "one-cancelled",
                "CANCEL_RECIPE",
                {"recipe_instance_id": current.menu[0].recipe_instance_id},
            )
        ).status
        == "APPLIED"
    )
    current = runtime.get("flow")
    request = prepare_replan(current, None, runtime.knowledge, current.policy)
    assert len(request.menu) == 1
    deadline = Deadline(expires_at_ns=time.monotonic_ns() + 2_000_000_000)
    forged = ProblemCompiler().compile(
        runtime.knowledge, (), request.runtime, current.policy, deadline
    )
    assert isinstance(forged, SchedulingProblem), forged
    candidate = GreedyScheduler().solve(forged, deadline).candidate
    assert candidate is not None
    proof = ScheduleValidator().validate(runtime.knowledge, forged.runtime, forged, candidate)
    assert proof.valid, proof
    state = current.runtime
    context = PublishContext(
        session_id=state.session_id,
        base_state_revision=state.state_revision,
        base_plan_version=state.current_plan_version,
        knowledge_version=state.knowledge_version,
        snapshot_id=state.snapshot_id,
        request_id="forged-empty",
        publication_id="forged-empty",
    )
    with pytest.raises(ValueError, match="完整菜单"):
        PlanPublisher(runtime.store, runtime.knowledge, runtime.clock, forged).publish(
            ValidatedSchedule(candidate=candidate, validation=proof), context, deadline=deadline
        )
    assert runtime.get("flow") == current
