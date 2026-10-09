"""新 P2 求解器直接消费发布知识，不调用旧离线排程工具。"""

import pytest

from app.domain.canonical_recipe import Dependency, OperationTemplate
from app.scheduling.cp_sat import CpSatScheduler
from app.validation.schedule import ScheduleValidator
from tests.compiler_support import published_knowledge
from tests.unit.test_cp_sat_model import deadline
from tests.unit.test_problem_compilation import compile_menu
from tests.validator_support import resource_example


@pytest.mark.parametrize("index", range(100))
def test_real_single_recipe_with_new_cp_sat(index):
    knowledge = published_knowledge()
    recipe = knowledge.recipes[index]
    problem = compile_menu(recipe)
    result = CpSatScheduler().solve(problem, None, deadline())
    assert result.status in {"OPTIMAL", "FEASIBLE"}, (recipe.name, result)
    report = ScheduleValidator().validate(knowledge, problem.runtime, problem, result.candidate)
    assert report.valid, (recipe.name, report.violations)


@pytest.mark.parametrize("tail_parent,expected", [(0, 260), (1, 320)])
def test_resource_order_changes_with_downstream_critical_path(tail_parent, expected):
    knowledge, state, _, _ = resource_example()
    recipe = knowledge.recipes[0]
    parent = recipe.operations[tail_parent]
    tail = OperationTemplate(
        operation_id="synthetic-tail", action="WAIT", duration={"execution_sec": 200}
    )
    recipe = recipe.model_copy(
        update={
            "operations": (*recipe.operations, tail),
            "dependencies": (
                Dependency(
                    predecessor_id=parent.operation_id,
                    successor_id=tail.operation_id,
                    min_lag_sec=0,
                    max_lag_sec=0,
                    reason="合成关键后续过程",
                    evidence_refs=("synthetic",),
                ),
            ),
        }
    )
    knowledge = knowledge.model_copy(update={"recipes": (recipe,)})
    problem = compile_menu(recipe, state=state, knowledge=knowledge)
    result = CpSatScheduler().solve(problem, None, deadline())
    assert result.status == "OPTIMAL", result
    assert result.objective_value == expected
    task = next(t for t in problem.logical_tasks if t.operation_id == parent.operation_id)
    assignment = next(a for a in result.candidate.assignments if task.task_id in a.task_ids)
    assert assignment.interval.start_sec == 0
    assert ScheduleValidator().validate(knowledge, state, problem, result.candidate).valid


@pytest.mark.parametrize("indices", [(0, 9, 51), (15, 87, 97), (74, 90), (86, 97, 84), (2, 43, 66)])
def test_published_multi_recipe_boundaries(indices):
    knowledge = published_knowledge()
    problem = compile_menu(*(knowledge.recipes[i] for i in indices))
    result = CpSatScheduler().solve(problem, None, deadline())
    assert result.status in {"OPTIMAL", "FEASIBLE"}, result
    report = ScheduleValidator().validate(knowledge, problem.runtime, problem, result.candidate)
    assert report.valid, report.violations
