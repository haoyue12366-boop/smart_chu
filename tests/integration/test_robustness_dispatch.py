"""合成启动窗口反例；实际事件和独立终态校验核对派发结果。"""

import time

import pytest

from app.compiler.compiler import ProblemCompiler
from app.domain.base import content_hash
from app.domain.canonical_recipe import CanonicalRecipeModel
from app.domain.ports import Deadline
from app.domain.runtime_facts import Occupancy
from app.domain.runtime_snapshot import ExecutionRecord
from app.domain.schedule import ValidatedSchedule
from app.domain.scheduling_problem import SchedulingProblem
from app.scheduling.greedy import GreedyScheduler
from app.scheduling.metrics import compute_metrics
from app.validation.schedule import ScheduleValidator
from benchmarks.robustness.observed_session import bind_plan, start_session
from benchmarks.robustness.simulator import FutureDurations, TrajectorySimulator
from tests.runtime_support import ORIGIN, p4_knowledge, policy, synthetic_knowledge


class SyntheticDurations:
    def __init__(self, mix_sec):
        self.mix_sec = mix_sec

    def duration(self, nominal, operations):
        operation = operations[0][1]
        if operation.operation_id.root == "mix":
            return self.mix_sec, True
        return nominal, operation.action != "MARINATE"


def witness(*, low=0, high=0, buffered=False, earliest_start=0):
    source = synthetic_knowledge()
    value = source.recipes[0].model_dump(mode="json")
    value["operations"][1]["duration"]["execution_sec"] = 60
    value["dependencies"] = [
        {
            "predecessor_id": "mix",
            "successor_id": "finish",
            "min_lag_sec": low,
            "max_lag_sec": high,
            "reason": "合成最大间隔汇合反例",
            "evidence_refs": ["synthetic:dispatch-window-witness"],
        },
        {
            "predecessor_id": "wait",
            "successor_id": "finish",
            "reason": "合成第二前驱",
            "evidence_refs": ["synthetic:dispatch-window-witness"],
        },
    ]
    knowledge = source.model_copy(update={"recipes": (CanonicalRecipeModel.model_validate(value),)})
    selected = policy().model_copy(
        update={"time_grid_sec": 1, "duration_policy_id": "BUFFERED" if buffered else "NOMINAL"}
    )
    session = start_session(
        knowledge, selected, {"case_id": "synthetic-window", "recipe_ids": ["synthetic-0"]}, ORIGIN
    )
    if earliest_start:
        details = session.runtime.details
        session = session.model_copy(
            update={
                "runtime": session.runtime.model_copy(
                    update={
                        "details": details.model_copy(
                            update={
                                "earliest_starts": (
                                    (session.menu[0].recipe_instance_id.root, earliest_start),
                                ),
                            }
                        ),
                    }
                )
            }
        )
    deadline = Deadline(expires_at_ns=time.monotonic_ns() + 5_000_000_000)
    problem = ProblemCompiler().compile(
        knowledge, session.menu, session.runtime, selected, deadline
    )
    assert isinstance(problem, SchedulingProblem), problem
    candidate = GreedyScheduler().solve(problem, deadline).candidate
    assert candidate is not None
    candidate = candidate.model_copy(update={"metrics": compute_metrics(candidate, problem)})
    proof = ScheduleValidator().validate(knowledge, session.runtime, problem, candidate)
    assert proof.valid, proof
    session = bind_plan(session, problem, ValidatedSchedule(candidate=candidate, validation=proof))
    return knowledge, session, problem, candidate


def simulator(*, mix_sec=120, **options):
    knowledge, session, problem, candidate = witness(**options)
    return TrajectorySimulator(
        knowledge,
        session,
        problem,
        candidate,
        SyntheticDurations(mix_sec),
        {"max_events": 100, "trigger_delay_sec": 30},
    )


def operation_span(result, operation_id, sim):
    task = next(t for t, (_, op) in sim.operations.items() if op.operation_id.root == operation_id)
    return next(
        p["interval"]
        for record in result["final_session"]["runtime"]["executions"]
        for p in record["task_spans"]
        if p["task_id"] == task.root
    )


@pytest.mark.parametrize("actual", [120, 180, 240])
def test_multi_predecessor_zero_gap_completes_early_nominal_and_late(actual):
    sim = simulator(mix_sec=actual)
    before = content_hash(sim.knowledge)
    result = sim.run()
    assert result["status"] == "COMPLETED", result["failure"]
    assert result["validation"]["valid"]
    assert operation_span(result, "finish", sim)["start_sec"] == actual
    assert result["completion_sec"] == actual + 60
    assert content_hash(sim.knowledge) == before


def test_tight_window_respects_minimum_and_maximum_actual_lag():
    sim = simulator(low=15, high=30)
    result = sim.run()
    assert result["status"] == "COMPLETED", result["failure"]
    finish = operation_span(result, "finish", sim)
    mix = operation_span(result, "mix", sim)
    assert 15 <= finish["start_sec"] - mix["end_sec"] <= 30
    assert result["validation"]["valid"]


def test_buffered_roots_keep_formal_not_before_and_fixed_wait_duration():
    sim = simulator(buffered=True)
    result = sim.run()
    assert result["status"] == "COMPLETED", result["failure"]
    buffer = sim.problem.duration_buffers[0]
    assert operation_span(result, "mix", sim)["start_sec"] >= buffer.not_before_sec
    wait = operation_span(result, "wait", sim)
    assert wait["start_sec"] >= buffer.not_before_sec
    assert wait["end_sec"] - wait["start_sec"] == 60
    assert result["validation"]["valid"]


def test_current_occupancy_is_not_released_at_its_predicted_end():
    sim = simulator()
    binding = next(b for b in sim.session.bindings if b.carrier.resource_uses)
    owner = ExecutionRecord(
        execution_id="synthetic-other-running",
        task_ids=("synthetic-other-task",),
        status="RUNNING",
        source="SIMULATED",
        event_refs=("synthetic-other-start",),
        started_at=ORIGIN,
        remaining_sec=1,
        remaining_observed_at=ORIGIN,
        remaining_source_ref="synthetic-other-start",
    )
    details = sim.session.runtime.details
    occupied = Occupancy(
        occupancy_id="synthetic-other-occupancy",
        execution_id=owner.execution_id,
        resource=binding.carrier.resource_uses[0],
        started_at=ORIGIN,
    )
    sim.session = sim.session.model_copy(
        update={
            "runtime": sim.session.runtime.model_copy(
                update={
                    "now_offset_sec": 10,
                    "executions": (owner,),
                    "details": details.model_copy(update={"occupancies": (occupied,)}),
                }
            )
        }
    )
    before = content_hash(sim.session)
    assert not any(stage.binding == binding for stage in sim.stages())
    assert content_hash(sim.session) == before


def test_missing_predecessor_confirmation_does_not_start_successor():
    sim = simulator()
    finish = next(t for t, (_, op) in sim.operations.items() if op.operation_id.root == "finish")
    assert all(finish not in stage.group for stage in sim.stages())


def test_dispatch_pause_keeps_new_execution_blocked():
    sim = simulator()
    sim.session = sim.session.model_copy(update={"dispatch_blocked": True})
    assert sim.stages() == []


def completed_prefix(sim):
    ready = {sim.operations[s.group[0]][1].operation_id.root: s for s in sim.stages()}
    sim.observe("OPERATION_STARTED", ready["mix"], 0)
    sim.observe("OPERATION_STARTED", ready["wait"], 0)
    sim.observe("OPERATION_COMPLETED", ready["wait"], 60)
    sim.observe("OPERATION_COMPLETED", ready["mix"], 120)


def test_expired_actual_window_fails_with_dependency_and_time_evidence():
    sim = simulator()
    completed_prefix(sim)
    sim.session = sim.session.model_copy(
        update={"runtime": sim.session.runtime.model_copy(update={"now_offset_sec": 121})}
    )
    result = sim.run()
    assert result["status"] == "FAILED"
    detail = result["failure_detail"]
    assert detail["code"] == "ACTUAL_WINDOW_EXPIRED"
    assert detail["now_sec"] == 121 and detail["latest_start_sec"] == 120
    assert detail["dependencies"][0]["actual_end_sec"] == 120
    assert detail["dependencies"][0]["max_lag_sec"] == 0


def test_remaining_execution_preserves_completed_history_and_records_plan_adjustment():
    sim = simulator()
    completed_prefix(sim)
    before = sim.session.runtime.executions
    result = sim.run()
    assert result["status"] == "COMPLETED", result["failure"]
    for record in before:
        current = next(
            e for e in sim.session.runtime.executions if e.execution_id == record.execution_id
        )
        assert current == record
    adjusted = [
        d for d in result["dispatch_diagnostics"] if d["code"] == "PLAN_ADJUSTED_WITHIN_WINDOW"
    ]
    assert adjusted
    assert adjusted[-1]["preferred_start_sec"] == 180
    assert adjusted[-1]["dispatch_at_sec"] == 120


def test_dispatch_pause_reports_plan_required():
    sim = simulator()
    sim.session = sim.session.model_copy(update={"dispatch_blocked": True})
    result = sim.run()
    assert result["status"] == "FAILED"
    assert result["failure_detail"]["code"] == "PLAN_REQUIRED"
    assert result["completed_task_count"] == 0


def test_explicit_recipe_delay_is_kept_separate_from_nominal_propagated_bounds():
    sim = simulator(earliest_start=200)
    result = sim.run()
    assert result["status"] == "COMPLETED", result["failure"]
    assert operation_span(result, "mix", sim)["start_sec"] >= 200
    assert operation_span(result, "wait", sim)["start_sec"] >= 200
    assert result["validation"]["valid"]


def test_predicted_completion_without_feedback_does_not_satisfy_dependency():
    sim = simulator()
    ready = {sim.operations[s.group[0]][1].operation_id.root: s for s in sim.stages()}
    sim.observe("OPERATION_STARTED", ready["mix"], 0)
    sim.observe("OPERATION_STARTED", ready["wait"], 0)
    sim.observe("OPERATION_COMPLETED", ready["wait"], 60)
    sim.session = sim.session.model_copy(
        update={
            "runtime": sim.session.runtime.model_copy(update={"now_offset_sec": 180}),
        }
    )
    result = sim.run()
    assert result["status"] == "FAILED"
    assert result["failure_detail"]["code"] == "OBSERVATION_REQUIRED"
    assert result["completed_task_count"] == 1
    finish = next(
        d
        for d in result["failure_detail"]["blocked_stages"]
        if any(dep["actual_end_sec"] is None for dep in d["dependencies"])
    )
    assert finish["code"] == "OBSERVATION_REQUIRED"


def test_same_time_completion_is_observed_before_reusing_human():
    source = synthetic_knowledge()
    first = source.recipes[0].model_dump(mode="json")
    first["operations"][0].update(action="MARINATE", resource_requirements=[])
    first["operations"][1]["duration"]["execution_sec"] = 60
    first["dependencies"] = [
        {
            "predecessor_id": "mix",
            "successor_id": "finish",
            "max_lag_sec": 0,
            "reason": "合成同刻资源释放反例",
            "evidence_refs": ["synthetic:same-time-release"],
        }
    ]
    second = source.recipes[1].model_dump(mode="json")
    second.update(operations=second["operations"][:1], dependencies=[])
    knowledge = source.model_copy(
        update={
            "recipes": (
                CanonicalRecipeModel.model_validate(first),
                CanonicalRecipeModel.model_validate(second),
            )
        }
    )
    selected = policy()
    initial = start_session(
        knowledge,
        selected,
        {
            "case_id": "synthetic-same-time",
            "recipe_ids": ["synthetic-0", "synthetic-1"],
        },
        ORIGIN,
    )
    limit = Deadline(expires_at_ns=time.monotonic_ns() + 5_000_000_000)
    problem = ProblemCompiler().compile(knowledge, initial.menu, initial.runtime, selected, limit)
    assert isinstance(problem, SchedulingProblem)
    candidate = GreedyScheduler().solve(problem, limit).candidate
    assert candidate is not None
    candidate = candidate.model_copy(update={"metrics": compute_metrics(candidate, problem)})
    proof = ScheduleValidator().validate(knowledge, initial.runtime, problem, candidate)
    assert proof.valid, proof
    session = bind_plan(initial, problem, ValidatedSchedule(candidate=candidate, validation=proof))
    sim = TrajectorySimulator(
        knowledge,
        session,
        problem,
        candidate,
        SyntheticDurations(180),
        {"max_events": 100, "trigger_delay_sec": 30},
    )
    task_map = {t.task_id: t for t in problem.logical_tasks}
    ready = sim.stages()
    passive = next(
        s
        for s in ready
        if task_map[s.group[0]].operation.action == "MARINATE"
        and task_map[s.group[0]].operation_id.root == "mix"
    )
    wait = next(s for s in ready if task_map[s.group[0]].operation_id.root == "wait")
    human = next(s for s in ready if task_map[s.group[0]].operation.action == "MIX")
    for stage in (passive, wait, human):
        sim.observe("OPERATION_STARTED", stage, 0)
    sim.observe("OPERATION_COMPLETED", wait, 60)
    sim.observe("OPERATION_COMPLETED", passive, 180)
    assert not sim.stages()  # 窗口尚未关闭，但人工尚无完成/释放确认。
    sim.observe("OPERATION_COMPLETED", human, 180)
    assert sim.stages()[0].at == 180
    result = sim.run()
    assert result["status"] == "COMPLETED", result["failure"]
    assert result["validation"]["valid"] and result["completion_sec"] == 240


@pytest.mark.parametrize("speed", [80, 100, 120])
def test_real_joint_carrier_keeps_internal_stages_and_fixed_heat_with_duration_changes(speed):
    source = p4_knowledge()
    ids = {"5c8200d96dc6e123a037a202", "5fe197175f8f38795ea6fe77"}
    knowledge = source.model_copy(
        update={
            "recipes": tuple(r for r in source.recipes if r.recipe_id.root in ids),
            "recipe_contexts": tuple(c for c in source.recipe_contexts if c.recipe_id.root in ids),
        }
    )
    selected = policy()
    initial = start_session(
        knowledge,
        selected,
        {
            "case_id": "real-h02-private-synthetic-duration",
            "recipe_ids": sorted(ids),
        },
        ORIGIN,
    )
    limit = Deadline(expires_at_ns=time.monotonic_ns() + 5_000_000_000)
    problem = ProblemCompiler().compile(knowledge, initial.menu, initial.runtime, selected, limit)
    assert isinstance(problem, SchedulingProblem)
    candidate = GreedyScheduler().solve(problem, limit).candidate
    assert candidate is not None
    candidate = candidate.model_copy(update={"metrics": compute_metrics(candidate, problem)})
    proof = ScheduleValidator().validate(knowledge, initial.runtime, problem, candidate)
    assert proof.valid, proof
    session = bind_plan(initial, problem, ValidatedSchedule(candidate=candidate, validation=proof))
    assert any(b.carrier.kind == "THERMAL_BATCH" for b in session.bindings)

    sim = TrajectorySimulator(
        knowledge,
        session,
        problem,
        candidate,
        FutureDurations(
            20261004,
            "real-h02-synthetic-duration",
            0,
            {
                "common_percent": [speed],
                "common_weights": [1],
                "local_percent": [100],
                "local_weights": [1],
            },
        ),
        {"max_events": 100, "trigger_delay_sec": 30},
    )
    result = sim.run()
    assert result["status"] == "COMPLETED", result["failure"]
    assert result["validation"]["valid"]
