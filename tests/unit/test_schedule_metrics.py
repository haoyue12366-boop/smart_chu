"""独立已知数字核验忙碌块、真实串行参考及身份稳定的扰动。"""

import pytest

from app.domain.schedule import CandidateSchedule
from app.domain.time import Interval
from app.scheduling.metrics import compute_disruption, compute_metrics
from app.scheduling.serial_reference import build_serial_reference
from app.validation.schedule import ScheduleValidator
from tests.compiler_support import published_knowledge
from tests.unit.test_cp_sat_model import deadline
from tests.unit.test_problem_compilation import compile_menu
from tests.unit.test_schedule_validator import example


@pytest.mark.parametrize("gap,expected", [(59, 179), (60, 60)])
def test_human_busy_gap_boundary(gap, expected):
    _, _, problem, candidate = example()
    first, second = candidate.assignments
    second = second.model_copy(update={"interval": Interval(start_sec=60 + gap, end_sec=120 + gap)})
    candidate = candidate.model_copy(update={"assignments": (first, second)})
    metrics = compute_metrics(candidate, problem)
    assert metrics.max_continuous_human_sec == expected
    assert metrics.total_human_work_sec == 120
    assert metrics.makespan_sec == 120 + gap
    assert metrics.serial_reference_sec is None


def test_disruption_uses_task_identity_and_ignores_new_tasks():
    _, _, problem, old = example()
    first, second = old.assignments
    current = old.model_copy(
        update={
            "assignments": (
                first,
                second.model_copy(update={"interval": Interval(start_sec=90, end_sec=150)}),
            )
        }
    )
    displacement = compute_disruption(current, old, problem)
    assert displacement.time_shift_sec == 30
    assert displacement.resource_changes == 0
    assert displacement.group_changes == 0
    new_only = CandidateSchedule(problem_hash=problem.problem_hash, assignments=(second,))
    previous = CandidateSchedule(problem_hash=problem.problem_hash, assignments=(first,))
    assert compute_disruption(new_only, previous, problem).time_shift_sec == 0


def test_real_serial_reference_keeps_long_preparation_and_fixed_program():
    knowledge = published_knowledge()
    recipes = [
        next(r for r in knowledge.recipes if r.name == name)
        for name in ("亲朋欢聚套餐", "韩式泡菜鸦片鱼头")
    ]
    problem = compile_menu(*recipes)
    result = build_serial_reference(problem, deadline())
    assert result.candidate is not None, result
    assert result.status == "CANDIDATE_FOUND"
    report = ScheduleValidator().validate(knowledge, problem.runtime, problem, result.candidate)
    assert report.valid, report.violations
    first_tasks = {
        t.task_id
        for t in problem.logical_tasks
        if t.recipe_instance_id == problem.recipe_instances[0].recipe_instance_id
    }
    first_end = max(
        a.interval.end_sec for a in result.candidate.assignments if first_tasks & set(a.task_ids)
    )
    assert first_end >= 14400
    assert all(
        a.interval.start_sec >= first_end
        for a in result.candidate.assignments
        if not first_tasks & set(a.task_ids)
    )


def test_no_budget_means_no_fake_serial_reference():
    _, _, problem, _ = example()
    result = build_serial_reference(problem, deadline(0))
    assert result.candidate is None
    assert result.status == "UNAVAILABLE"


def test_replan_human_busy_block_keeps_actual_history_start():
    from app.scheduling.cp_sat import CpSatScheduler
    from tests.unit.test_compiler_material_stock import completed_case

    knowledge, state, _, _ = completed_case()
    problem = compile_menu(knowledge.recipes[0], state=state, knowledge=knowledge)
    result = CpSatScheduler().solve(problem, None, deadline())
    metrics = compute_metrics(result.candidate, problem)
    assert metrics.actual_human_work_sec == 60
    assert metrics.remaining_human_work_sec == 60
    assert metrics.remaining_makespan_sec == 60
    assert metrics.remaining_max_continuous_human_sec == 120


def test_validator_recomputes_reported_metrics_instead_of_trusting_them():
    from app.domain.schedule import ScheduleMetrics

    knowledge, state, problem, candidate = example()
    correct = compute_metrics(candidate, problem)
    candidate = candidate.model_copy(update={"metrics": correct})
    assert ScheduleValidator().validate(knowledge, state, problem, candidate).valid
    bad = ScheduleMetrics(makespan_sec=1, completion_spread_sec=0, max_continuous_human_sec=0)
    candidate = candidate.model_copy(update={"metrics": bad})
    report = ScheduleValidator().validate(knowledge, state, problem, candidate)
    assert "METRICS" in {v.code for v in report.violations}
