"""可选缓冲的数值、工艺、来源和独立拒绝边界。"""

import time

import pytest
from pydantic import ValidationError

from app.compiler.compiler import ProblemCompiler
from app.domain.base import content_hash
from app.domain.duration_estimate import DurationProblemInputs, DurationRoot
from app.domain.ports import Deadline
from app.domain.runtime_snapshot import ExecutionRecord
from app.domain.scheduling_problem import SchedulingProblem
from app.scheduling.duration_policy import DurationPolicy, estimated_phase, source_estimates
from app.scheduling.greedy import GreedyScheduler
from app.scheduling.metrics import compute_metrics
from app.validation.schedule import ScheduleValidator
from tests.compiler_support import menu_for, runtime
from tests.runtime_support import policy, synthetic_knowledge


def deadline():
    return Deadline(expires_at_ns=time.monotonic_ns() + 5_000_000_000)


def setup():
    source = synthetic_knowledge()
    source = source.model_copy(update={"recipes": source.recipes[:1]})
    menu = menu_for(source.recipes[0])
    state = runtime(source)
    buffered = policy().model_copy(
        update={"duration_policy_id": "BUFFERED", "duration_data_version": "synthetic-buffer-v1"}
    )
    return source, menu, state, buffered


def inputs():
    knowledge, menu, state, buffered = setup()
    nominal = ProblemCompiler().compile(knowledge, menu, state, policy(), deadline())
    assert isinstance(nominal, SchedulingProblem)
    task = nominal.logical_tasks[0]
    value = DurationProblemInputs(
        knowledge=knowledge,
        policy=buffered,
        now_offset_sec=0,
        roots=(
            DurationRoot(
                recipe_instance_id=menu[0].recipe_instance_id,
                recipe_id=menu[0].recipe_id,
                task_id=task.task_id,
                operation_id=task.operation_id.root,
                earliest_start_sec=0,
                instance_already_started=False,
            ),
        ),
    )
    return value


def test_buffer_is_explicit_wait_without_resource_or_work_inflation():
    value = inputs()
    before = content_hash(value.knowledge)
    result = DurationPolicy().apply(value, source_estimates(value))
    assert result.inputs == value
    assert content_hash(value.knowledge) == before
    assert len(result.buffers) == 1
    assert result.buffers[0].buffer_sec == 36  # (180+60)*15%; 1200秒腌制不加缓冲
    assert not result.buffers[0].resource_reservations
    assert all(
        estimate.sample_count == 0 and estimate.empirical_p90_sec is None
        for estimate in result.estimates
    )


def test_heat_and_fixed_process_are_protected_even_when_manual():
    operation = synthetic_knowledge().recipes[0].operations[0]
    heat = operation.model_copy(update={"action": "HEAT"})
    fixed = operation.model_copy(
        update={"duration": operation.duration.model_copy(update={"fixed_process_time": True})}
    )
    assert estimated_phase(heat) == estimated_phase(fixed) == "FIXED_PROCESS"


def test_nominal_inputs_are_unchanged_and_have_no_buffer():
    value = inputs()
    value = value.model_copy(update={"policy": policy()})
    result = DurationPolicy().apply(value, source_estimates(value))
    assert result.inputs == value and result.buffers == ()


@pytest.mark.parametrize("damage", ["duration", "version", "operation", "missing", "duplicate"])
def test_estimate_changes_cannot_hide_source_or_version_mismatch(damage):
    value = inputs()
    estimates = source_estimates(value)
    if damage == "missing":
        estimates = estimates[:-1]
    elif damage == "duplicate":
        estimates = (*estimates, estimates[0])
    else:
        change = {
            "duration": {"nominal_sec": 181},
            "version": {"data_version": "future"},
            "operation": {"operation_hash": "0" * 64},
        }[damage]
        estimates = (estimates[0].model_copy(update=change), *estimates[1:])
    with pytest.raises(ValueError):
        DurationPolicy().apply(value, estimates)


def test_empirical_quantiles_require_real_comparable_samples():
    estimate = source_estimates(inputs())[0]
    with pytest.raises(ValidationError, match="30"):
        type(estimate).model_validate(
            {**estimate.model_dump(mode="json"), "empirical_p90_sec": 200}
        )


def test_buffered_compiler_greedy_and_independent_validator_keep_source_durations():
    knowledge, menu, state, buffered = setup()
    nominal = ProblemCompiler().compile(knowledge, menu, state, policy(), deadline())
    problem = ProblemCompiler().compile(knowledge, menu, state, buffered, deadline())
    assert isinstance(nominal, SchedulingProblem) and isinstance(problem, SchedulingProblem)
    assert problem.problem_hash != nominal.problem_hash
    assert tuple(task.operation for task in problem.logical_tasks) == tuple(
        task.operation for task in nominal.logical_tasks
    )
    assert problem.dependencies == nominal.dependencies
    candidate = GreedyScheduler().solve(problem, deadline()).candidate
    assert candidate is not None
    proof = ScheduleValidator().validate(knowledge, state, problem, candidate)
    assert proof.valid, proof.violations
    assert compute_metrics(candidate, problem).total_human_work_sec == 240
    assert min(assignment.interval.start_sec for assignment in candidate.assignments) >= 36
    corrupt = problem.model_copy(update={"duration_buffers": ()})
    proof = ScheduleValidator().validate(
        knowledge,
        state,
        corrupt,
        candidate.model_copy(update={"problem_hash": corrupt.problem_hash}),
    )
    assert not proof.valid and "DURATION_POLICY" in {
        violation.code for violation in proof.violations
    }


def test_started_recipe_has_no_new_buffer_and_fixed_actuals_are_preserved():
    knowledge, menu, state, buffered = setup()
    before = ProblemCompiler().compile(knowledge, menu, state, policy(), deadline())
    assert isinstance(before, SchedulingProblem)
    task = before.logical_tasks[0]
    record = ExecutionRecord(
        execution_id="synthetic-running",
        task_ids=(task.task_id,),
        status="RUNNING",
        source="SIMULATED",
        event_refs=("synthetic-start",),
        started_at=state.time_origin.start_at,
        remaining_sec=170,
        remaining_source_ref="synthetic:observed",
        resource_ids=("human_1",),
    )
    state = state.model_copy(update={"now_offset_sec": 10, "executions": (record,)})
    problem = ProblemCompiler().compile(knowledge, menu, state, buffered, deadline())
    assert isinstance(problem, SchedulingProblem), problem
    assert not problem.duration_buffers
    assert problem.fixed_executions == (record,)
    assert knowledge.recipes[0].operations[0].duration.execution_sec == 180
