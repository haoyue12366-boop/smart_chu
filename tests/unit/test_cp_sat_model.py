"""独立枚举的小问题、提示语义和真实求解状态。"""

import time

import pytest

from app.domain.ports import Deadline
from app.scheduling.cp_sat import CpSatScheduler
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
