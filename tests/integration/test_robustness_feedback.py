"""持续反馈使用真实事件/投影/校验；合成观察不读取未发生的未来。"""

import time

import pytest

from app.compiler.compiler import ProblemCompiler
from app.domain.base import content_hash
from app.domain.events import ExecutionPayload
from app.domain.ports import Deadline
from app.domain.schedule import ValidatedSchedule
from app.runtime.replanning import prepare_replan
from app.scheduling.greedy import GreedyScheduler
from app.scheduling.metrics import compute_metrics
from app.validation.schedule import ScheduleValidator
from benchmarks.robustness.feedback import ContinuousTrajectorySimulator
from benchmarks.robustness.observed_session import bind_plan, event
from tests.integration.test_robustness_dispatch import witness


class PrivateDurations:
    def __init__(self, mix, finish):
        self.values = {"mix": mix, "finish": finish}

    def duration(self, nominal, operations):
        op = operations[0][1]
        return self.values.get(op.operation_id.root, nominal), op.action != "MARINATE"


def simulate(mix, finish, planner):
    knowledge, session, problem, candidate = witness()
    return ContinuousTrajectorySimulator(
        knowledge,
        session,
        problem,
        candidate,
        PrivateDurations(mix, finish),
        {"max_events": 100, "trigger_delay_sec": 30, "feedback_extension_sec": 30},
        planner=planner,
    )


def actual_planner(knowledge, session):
    request = prepare_replan(session, None, knowledge, session.policy)
    deadline = Deadline(expires_at_ns=time.monotonic_ns() + 2_400_000_000)
    problem = ProblemCompiler().compile(
        knowledge, request.menu, request.runtime, request.policy, deadline
    )
    candidate = GreedyScheduler().solve(problem, deadline).candidate
    assert candidate is not None
    candidate = candidate.model_copy(update={"metrics": compute_metrics(candidate, problem)})
    proof = ScheduleValidator().validate(knowledge, request.runtime, problem, candidate)
    assert proof.valid, proof
    return (
        bind_plan(session, problem, ValidatedSchedule(candidate=candidate, validation=proof)),
        problem,
        {"failure": None, "input_state_revision": session.runtime.state_revision},
    )


def test_later_delay_triggers_when_first_stage_is_nominal():
    observed = []

    def stop(knowledge, session):
        observed.append(session)
        return None, None, {"failure": "explicit stop after observing trigger"}

    result = simulate(180, 90, stop).run()
    assert result["replan_count"] == 1
    assert observed[0].runtime.now_offset_sec == 240
    assert result["event_counts"]["DURATION_UPDATED"] == 1
    assert result["feedback_triggers"][0]["reasons"] == ["REMAINING_DURATION_CHANGED"]


def test_multiple_deviations_get_multiple_replans_and_independent_terminal_proof():
    result = simulate(200, 100, actual_planner).run()
    assert result["status"] == "COMPLETED", result["failure"]
    assert result["replan_count"] >= 3
    assert result["event_counts"]["DURATION_UPDATED"] >= 2
    assert result["validation"]["valid"]
    assert result["protected_stage_count"] == 1


def test_same_observed_prefix_different_private_future_has_same_feedback_input():
    observed = []

    def stop(knowledge, session):
        observed.append(content_hash(session))
        return None, None, {"failure": "explicit stop"}

    results = [simulate(mix, finish, stop).run() for mix, finish in [(240, 30), (500, 900)]]
    assert len(observed) == 2 and observed[0] == observed[1]
    assert all(r["events"][-1]["event_type"] == "DURATION_UPDATED" for r in results)
    assert all(r["events"][-1]["payload"]["remaining_sec"] == 30 for r in results)


def test_stale_replan_publication_is_rejected():
    def stale(knowledge, session):
        updated, problem, measurement = actual_planner(knowledge, session)
        return (
            updated.model_copy(
                update={"runtime": updated.runtime.model_copy(update={"state_revision": 999})}
            ),
            problem,
            measurement,
        )

    result = simulate(200, 60, stale).run()
    assert result["status"] == "FAILED"
    assert result["failure_detail"]["code"] == "STALE_REPLAN_RESULT"
    assert result["final_session"]["runtime"]["state_revision"] != 999


def test_same_time_completion_precedes_feedback_probe_and_does_not_create_false_overrun():
    result = simulate(180, 60, actual_planner).run()
    assert result["status"] == "COMPLETED", result["failure"]
    assert result["replan_count"] == 0
    assert result["event_counts"].get("DURATION_UPDATED", 0) == 0


def test_repeated_feedback_is_idempotent_and_preserves_current_occupancy():
    sim = simulate(240, 60, None)
    stage = next(
        s for s in sim.stages() if sim.operations[s.group[0]][1].operation_id.root == "mix"
    )
    sim.observe("OPERATION_STARTED", stage, stage.at)
    request = event(
        sim.session,
        "repeat-visible-feedback",
        "DURATION_UPDATED",
        {"task_id": stage.group[0], "execution_id": stage.execution_id, "remaining_sec": 150},
        90,
    )
    occupations = sim.session.runtime.details.occupancies
    assert sim.apply_feedback(request)
    after = content_hash(sim.session)
    assert not sim.apply_feedback(request)
    assert content_hash(sim.session) == after
    assert sim.session.runtime.details.occupancies == occupations
    altered = request.model_copy(
        update={
            "payload": ExecutionPayload.model_validate(
                {**request.payload.model_dump(), "remaining_sec": 1}
            )
        }
    )
    with pytest.raises(ValueError, match="同一反馈身份"):
        sim.apply_feedback(altered)
    assert content_hash(sim.session) == after


def test_feedback_cannot_shorten_fixed_wait_or_create_completion():
    sim = simulate(180, 60, None)
    stage = next(
        s for s in sim.stages() if sim.operations[s.group[0]][1].operation_id.root == "wait"
    )
    sim.observe("OPERATION_STARTED", stage, stage.at)
    before = content_hash(sim.session)
    request = event(
        sim.session,
        "invalid-fixed-feedback",
        "DURATION_UPDATED",
        {"task_id": stage.group[0], "execution_id": stage.execution_id, "remaining_sec": 0},
        30,
    )
    with pytest.raises(ValueError, match="固定工艺"):
        sim.apply_feedback(request)
    assert content_hash(sim.session) == before
