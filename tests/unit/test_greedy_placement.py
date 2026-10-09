"""Greedy 的共同空档、物料与固定工艺，不借用 CP-SAT 实现。"""

from app.scheduling.greedy import GreedyScheduler
from app.validation.schedule import ScheduleValidator
from tests.compiler_support import published_knowledge
from tests.unit.test_cp_sat_model import deadline
from tests.unit.test_problem_compilation import compile_menu
from tests.validator_support import resource_example


def test_greedy_shared_state_allows_different_durations_and_is_deterministic():
    data = resource_example("STATE_COMPATIBLE")
    knowledge, state, problem, _ = data
    first = GreedyScheduler().solve(problem, deadline())
    second = GreedyScheduler().solve(problem, deadline())
    assert first.status == "CANDIDATE_FOUND", first
    assert first.candidate == second.candidate
    assert max(a.interval.end_sec for a in first.candidate.assignments) == 120
    assert ScheduleValidator().validate(knowledge, state, problem, first.candidate).valid


def test_greedy_common_human_cannot_overlap_on_separate_devices():
    knowledge, state, problem, _ = resource_example(independent=True, human=True)
    result = GreedyScheduler().solve(problem, deadline())
    assert result.status == "CANDIDATE_FOUND", result
    assert max(a.interval.end_sec for a in result.candidate.assignments) == 180
    assert ScheduleValidator().validate(knowledge, state, problem, result.candidate).valid


def test_greedy_preserves_real_fixed_program_in_multi_recipe_menu():
    knowledge = published_knowledge()
    recipes = [
        next(r for r in knowledge.recipes if r.name == name)
        for name in ("亲朋欢聚套餐", "烹香酷炒汇", "韩式泡菜鸦片鱼头")
    ]
    problem = compile_menu(*recipes)
    result = GreedyScheduler().solve(problem, deadline())
    assert result.status == "CANDIDATE_FOUND", result
    report = ScheduleValidator().validate(knowledge, problem.runtime, problem, result.candidate)
    assert report.valid, report.violations


def test_greedy_timeout_is_not_infeasibility_and_does_not_return_partial_plan():
    _, _, problem, _ = resource_example()
    result = GreedyScheduler().solve(problem, deadline(0))
    assert result.status == "BUDGET_EXHAUSTED"
    assert result.candidate is None


def test_single_placement_after_frozen_fact_and_rollback_keeps_history():
    from app.scheduling.calendars import CalendarState
    from app.scheduling.placement import find_earliest_feasible_placement
    from tests.unit.test_compiler_material_stock import completed_case

    knowledge, state, _, _ = completed_case()
    problem = compile_menu(knowledge.recipes[0], state=state, knowledge=knowledge)
    calendars = CalendarState(problem)
    before = calendars.state_hash
    placement = find_earliest_feasible_placement(
        problem.standalone_candidates[0], calendars, problem, deadline()
    )
    assert placement.assignments, placement.rejection_reasons
    assert placement.assignments[0].interval.start_sec == 60
    calendars.commit(placement)
    calendars.rollback()
    assert before == calendars.state_hash
    assert calendars.current.covered == problem.fixed_executions[0].task_ids


def test_failed_variant_does_not_prevent_other_variants(monkeypatch):
    import app.scheduling.greedy as module

    knowledge, state, problem, _ = resource_example()
    original = module.recipe_layout

    def fail_first(problem, instance, variant, deadline):
        if variant == 0:
            raise ValueError("合成：第一个有限布局失败")
        return original(problem, instance, variant, deadline)

    monkeypatch.setattr(module, "recipe_layout", fail_first)
    result = GreedyScheduler().solve(problem, deadline())
    assert result.status == "CANDIDATE_FOUND", result
    assert ScheduleValidator().validate(knowledge, state, problem, result.candidate).valid


def test_one_failed_layout_is_not_retried_without_new_search_state():
    knowledge, state, problem, _ = resource_example()
    tasks = tuple(t.model_copy(update={"latest_end_sec": 120}) for t in problem.logical_tasks)
    problem = problem.model_copy(update={"logical_tasks": tasks})
    result = GreedyScheduler().solve(problem, deadline())
    assert result.status == "CONSTRUCTION_FAILED"
    assert result.candidate is None


def test_failed_placements_count_toward_per_iteration_limit(monkeypatch):
    import app.scheduling.greedy as module
    from app.scheduling.calendar_types import PlacementResult

    knowledge, state, original, _ = resource_example()
    limits = original.policy.greedy_search.model_copy(
        update={"max_variants": 1, "max_standalone_restarts": 0, "max_placements_per_iteration": 2}
    )
    policy = original.policy.model_copy(update={"greedy_search": limits})
    problem = compile_menu(
        *([knowledge.recipes[0]] * 5), state=state, knowledge=knowledge, policy=policy
    )
    attempts = []

    def reject(*args):
        attempts.append(1)
        return PlacementResult(rejection_reasons=("合成：无可用空档",))

    monkeypatch.setattr(module, "find_layout_placement", reject)
    result = GreedyScheduler().solve(problem, deadline())
    assert result.status == "CONSTRUCTION_FAILED"
    assert len(attempts) == 2
