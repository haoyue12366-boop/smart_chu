"""独立枚举的小问题、提示语义和真实求解状态。"""

import time

import pytest

from app.domain.objectives import ObjectiveStage
from app.domain.ports import Deadline
from app.domain.time import Interval
from app.scheduling.cp_sat import CpSatScheduler
from app.scheduling.metrics import compute_metrics
from app.validation.schedule import ScheduleValidator
from tests.unit.test_schedule_validator import example
from tests.validator_support import resource_example


def deadline(seconds=5):
    return Deadline(expires_at_ns=time.monotonic_ns() + int(seconds * 1_000_000_000))


@pytest.mark.parametrize(
    "policy,independent,human,different,expected",
    [
        ("UNARY", False, False, False, 180),
        ("UNARY", True, False, False, 120),
        ("UNARY", True, True, False, 180),
        ("BATCH_EXCLUSIVE", False, False, False, 180),
        ("STATE_COMPATIBLE", False, False, False, 120),
        ("STATE_COMPATIBLE", False, False, True, 180),
        ("SHARED_AUXILIARY", False, False, False, 120),
        ("SHARED_AUXILIARY", False, False, True, 180),
    ],
)
def test_two_task_optimum_equals_independent_enumeration(
    policy, independent, human, different, expected
):
    knowledge, state, problem, _ = resource_example(
        policy, independent=independent, human=human, different=different
    )
    solver = CpSatScheduler()
    result = solver.solve(problem, None, deadline())
    assert result.status == "OPTIMAL", result
    assert result.objective_value == expected
    report = ScheduleValidator().validate(knowledge, state, problem, result.candidate)
    assert report.valid, report.violations
    assert solver.last_build_report.problem_hash == problem.problem_hash
    assert solver.last_build_report.serialized_proto_bytes > 0
    assert solver.last_build_report.constraint_mappings


def test_hint_is_not_a_hard_constraint_and_model_is_immutable():
    knowledge, state, problem, invalid_hint = resource_example()
    before = problem.problem_hash
    result = CpSatScheduler().solve(problem, invalid_hint, deadline())
    assert result.status == "OPTIMAL"
    assert result.objective_value == 180
    assert before == problem.problem_hash
    assert ScheduleValidator().validate(knowledge, state, problem, result.candidate).valid


def test_complete_hint_outside_stage_bounds_does_not_bias_its_paths(monkeypatch):
    from ortools.sat.python import cp_model

    knowledge, state, problem, candidate = resource_example(independent=True, human=True)
    first, second = candidate.assignments
    hint = candidate.model_copy(
        update={
            "assignments": (
                first,
                second.model_copy(update={"interval": Interval(start_sec=120, end_sec=240)}),
            )
        }
    )
    hint = hint.model_copy(update={"metrics": compute_metrics(hint, problem)})
    assert ScheduleValidator().validate(knowledge, state, problem, hint).valid
    captured = []
    original = cp_model.CpSolver.solve

    def observe(solver, model, *args, **kwargs):
        captured.append(tuple(model.proto.solution_hint.vars))
        return original(solver, model, *args, **kwargs)

    monkeypatch.setattr(cp_model.CpSolver, "solve", observe)
    result = CpSatScheduler().solve(
        problem, hint, deadline(), stage=ObjectiveStage(name="C_MAKESPAN", makespan_cap_sec=180)
    )
    assert result.status == "OPTIMAL" and result.objective_value == 180
    assert ScheduleValidator().validate(knowledge, state, problem, result.candidate).valid
    assert captured == [()]


def test_budget_exhaustion_is_unknown_without_fake_candidate():
    _, _, problem, _ = example()
    result = CpSatScheduler().solve(problem, None, deadline(0))
    assert result.status == "UNKNOWN"
    assert result.candidate is None


def test_impossible_time_window_is_infeasible_not_model_invalid():
    _, _, problem, _ = resource_example()
    tasks = tuple(t.model_copy(update={"latest_end_sec": 120}) for t in problem.logical_tasks)
    problem = problem.model_copy(update={"logical_tasks": tasks})
    result = CpSatScheduler().solve(problem, None, deadline())
    assert result.status == "INFEASIBLE", result
    assert result.candidate is None
