"""合成双菜验证备料声明允许未来操作错峰，仍受单人及硬窗口约束。"""

import pytest

from app.domain.base import content_hash
from app.domain.ids import RecipeId
from app.runtime.menu_events import apply_menu
from app.scheduling.greedy import GreedyScheduler
from app.validation.schedule import ScheduleValidator
from tests.runtime_support import event
from tests.unit.test_advance_preparation import compile_case, preparation_case
from tests.unit.test_cp_sat_model import deadline


def prepared_menu(at=0):
    knowledge, session = preparation_case()
    original = knowledge.recipes[0]
    second = original.model_copy(
        update={"recipe_id": RecipeId("synthetic-other-prepared"), "name": "合成第二道提前浸泡"}
    )
    knowledge = knowledge.model_copy(update={"recipes": (original, second)})
    rule = session.policy.advance_preparation_rules[0]
    second_rule = rule.model_copy(
        update={
            "rule_id": "synthetic:second-soak",
            "recipe_id": second.recipe_id,
            "recipe_hash": content_hash(second),
        }
    )
    session = session.model_copy(
        update={
            "policy": session.policy.model_copy(
                update={"advance_preparation_rules": (rule, second_rule)}
            )
        }
    )
    session = apply_menu(
        session,
        event(
            session,
            "two-prepared",
            "START_SESSION",
            {"recipes": [{"id": r.recipe_id.root, "name": r.name} for r in knowledge.recipes]},
            at=at,
        ),
        knowledge,
    )
    session = session.model_copy(
        update={"runtime": session.runtime.model_copy(update={"now_offset_sec": at})}
    )
    return knowledge, compile_case(knowledge, session)


@pytest.mark.parametrize("at", [0, 90])
def test_prepared_recipes_can_move_future_operations_to_avoid_human_conflicts(at):
    knowledge, problem = prepared_menu(at)
    assert len(problem.advance_preparations) == 2
    assert not problem.fixed_executions
    result = GreedyScheduler().solve(problem, deadline())
    assert result.candidate is not None, result.reason
    proof = ScheduleValidator().validate(knowledge, problem.runtime, problem, result.candidate)
    assert proof.valid, proof.violations
    starts = [assignment.interval.start_sec for assignment in result.candidate.assignments]
    assert min(starts) >= at
    tasks = {task.task_id: task for task in problem.logical_tasks}
    heat_starts = sorted(
        assignment.interval.start_sec
        for assignment in result.candidate.assignments
        if tasks[assignment.task_ids[0]].operation_id.root == "heat"
    )
    assert heat_starts[1] >= heat_starts[0] + 60


def test_preparation_does_not_allow_moving_operations_past_a_hard_window():
    _, problem = prepared_menu()
    problem = problem.model_copy(
        update={
            "logical_tasks": tuple(
                task.model_copy(update={"latest_end_sec": 60})
                if task.operation_id.root == "heat"
                else task
                for task in problem.logical_tasks
            )
        }
    )
    result = GreedyScheduler().solve(problem, deadline())
    assert result.candidate is None
    assert result.status == "CONSTRUCTION_FAILED"
