"""真实复杂菜单逐道构造必须满足工艺/物料约束；冻结事实不被平移。"""

import json

import pytest

from app.compiler.compiler import ProblemCompiler
from app.config import ROOT, AppSettings
from app.domain.policy import SchedulingPolicy
from app.domain.scheduling_problem import SchedulingProblem
from app.scheduling.greedy import GreedyScheduler
from app.scheduling.serial_reference import serial_order_holds
from app.validation.schedule import ScheduleValidator
from tests.compiler_support import menu_for, runtime
from tests.runtime_support import p4_knowledge
from tests.unit.test_compiler_material_stock import completed_case
from tests.unit.test_cp_sat_model import deadline
from tests.unit.test_problem_compilation import compile_menu


@pytest.mark.parametrize("case_id", ["replan-004", "replan-016"])
def test_real_five_dish_serial_reference_is_complete_and_independently_legal(case_id):
    suite = json.loads((ROOT / "benchmarks/scenarios/full_suite.json").read_bytes())
    case = next(c for c in suite["replans"] if c["case_id"] == case_id)
    knowledge = p4_knowledge()
    by_id = {r.recipe_id.root: r for r in knowledge.recipes}
    identities = [*case["recipe_ids"], case["event_script"]["additional_recipe_id"]]
    menu = menu_for(*(by_id[i] for i in identities))
    state = runtime(knowledge)
    policy = SchedulingPolicy.model_validate_json(AppSettings().policy_path.read_bytes())
    problem = ProblemCompiler().compile(knowledge, menu, state, policy, deadline(4))
    assert isinstance(problem, SchedulingProblem)
    result = GreedyScheduler().solve_serial(problem, deadline(2))
    assert result.candidate is not None, result
    proof = ScheduleValidator().validate(knowledge, state, problem, result.candidate)
    assert proof.valid, proof.violations
    assert serial_order_holds(result.candidate, problem)
    assert len(result.candidate.recipe_completions) == len(menu)


def test_serial_constructor_defers_frozen_history_without_moving_completed_facts():
    knowledge, state, _, _ = completed_case()
    problem = compile_menu(knowledge.recipes[0], state=state, knowledge=knowledge)
    before = problem.model_dump_json()
    result = GreedyScheduler().solve_serial(problem, deadline())
    assert result.candidate is None
    assert result.status == "CONSTRUCTION_FAILED"
    assert problem.model_dump_json() == before
