"""合成独立下界见证FT两阶段目标；局部搜索约束不成为执行事实。"""

import pytest
from pydantic import ValidationError

from app.config import AppSettings
from app.domain.objectives import ObjectiveStage
from app.scheduling.cp_sat import CpSatScheduler
from app.scheduling.engine import PlanningEngine
from app.validation.schedule import ScheduleValidator
from tests.unit.test_cp_sat_model import deadline
from tests.unit.test_prepared_greedy import prepared_menu


def test_new_service_defaults_to_ft_and_can_explicitly_use_original_strategy(monkeypatch):
    assert AppSettings().scheduling_strategy == "FT_KITCHEN"
    monkeypatch.setenv("SMART_COOKING_SCHEDULING_STRATEGY", "FULL_QUALITY")
    assert AppSettings.from_environment().scheduling_strategy == "FULL_QUALITY"


def ft_case(at=0):
    from app.services.deployment_policy import scheduling_strategy_policy

    knowledge, original = prepared_menu(at)
    selected = scheduling_strategy_policy(original.policy, "FT_KITCHEN")
    return knowledge, original.model_copy(update={"policy": selected}), original


def test_ft_policy_keeps_facts_and_budgets_and_has_a_distinct_identity():
    _, problem, original = ft_case()
    assert problem.policy.search_strategy == "FT_KITCHEN"
    assert not problem.policy.quality_first
    assert problem.policy.objective.spread_basis == "WORKFLOW_FINISH"
    assert problem.policy.objective.spread_target_sec == 0
    assert problem.policy.objective.rest_gap_sec == 300
    assert problem.policy.objective.makespan_extra_basis_points == 0
    assert problem.policy.objective.makespan_extra_cap_sec == 0
    assert problem.policy.policy_version != original.policy.policy_version
    assert problem.policy.initial_budget == original.policy.initial_budget
    assert problem.policy.replan_budget == original.policy.replan_budget
    assert problem.advance_preparations == original.advance_preparations
    assert problem.policy.human_count == 1
    assert "search_strategy" not in original.policy.model_dump(mode="json")


@pytest.mark.parametrize("stage_name", ["A_MAKESPAN", "B_SPREAD"])
@pytest.mark.parametrize("ft", [False, True])
def test_ft_time_stages_use_lightweight_search_without_changing_original_parameters(
    monkeypatch, stage_name, ft
):
    from ortools.sat.python import cp_model

    _, problem, original_problem = ft_case()
    problem = problem if ft else original_problem
    parameters = []
    original = cp_model.CpSolver

    class RecordingSolver(original):
        def solve(self, model, solution_callback=None):
            parameters.append(
                (self.parameters.cp_model_probing_level, self.parameters.linearization_level)
            )
            return super().solve(model, solution_callback)

    monkeypatch.setattr(cp_model, "CpSolver", RecordingSolver)
    result = CpSatScheduler().solve(
        problem, None, deadline(), stage=ObjectiveStage(name=stage_name)
    )
    assert result.status == "OPTIMAL"
    assert parameters == [(0, 0) if ft else (2, 1)]


@pytest.mark.parametrize("replan", [False, True])
def test_ft_search_keeps_minimum_makespan_then_minimizes_actual_completion_spread(replan):
    knowledge, problem, _ = ft_case()
    if replan:
        details = problem.runtime.details.model_copy(update={"planning_kind": "REPLAN"})
        problem = problem.model_copy(
            update={"runtime": problem.runtime.model_copy(update={"details": details})}
        )
    original = problem.model_dump_json()
    calls = []
    native = CpSatScheduler()

    class RecordingSolver:
        def solve(self, problem, hint, limit, *, stage=None, serial_menu=False):
            calls.append(stage)
            return native.solve(problem, hint, limit, stage=stage, serial_menu=serial_menu)

    result = PlanningEngine(validator=ScheduleValidator(), solver=RecordingSolver()).plan(
        problem, knowledge, problem.runtime, deadline()
    )
    assert result.status == "VALIDATED", result.failure
    stages = [stage for stage in calls if stage is not None]
    assert [stage.name for stage in stages] == ["A_MAKESPAN", "B_SPREAD"] + (
        ["D_HUMAN"] if replan else []
    )
    assert stages[1].makespan_cap_sec == 1350
    if replan:
        assert stages[-1].thermal_seed is not None
        assert stages[-1].thermal_seed.problem_hash == problem.problem_hash
    assert problem.model_dump_json() == original
    # 两个60秒加热共用单人：第二个不早于60；再冷却1200、收尾30，
    # 总流程独立下界1350可达。两个互斥30秒收尾的完成差下界30也可达。
    assert result.candidate.metrics.makespan_sec == 1350
    assert result.candidate.metrics.completion_spread_sec == 30
    for candidate in (result.candidate, result.serial_reference_candidate):
        assert ScheduleValidator().validate(knowledge, problem.runtime, problem, candidate).valid
    assert result.human_objective_optimized is replan


def test_thermal_neighborhood_is_only_legal_for_human_search_and_survives_transport():
    from app.scheduling.greedy import GreedyScheduler

    _, problem, _ = ft_case()
    candidate = GreedyScheduler().solve(problem, deadline()).candidate
    stage = ObjectiveStage(name="D_HUMAN", thermal_seed=candidate)
    assert ObjectiveStage.model_validate_json(stage.model_dump_json()) == stage
    with pytest.raises(ValidationError):
        ObjectiveStage(name="A_MAKESPAN", thermal_seed=candidate)
    assert "thermal_seed" not in ObjectiveStage(name="D_HUMAN").model_dump(mode="json")
    wrong = candidate.model_copy(update={"problem_hash": "0" * 64})
    result = CpSatScheduler().solve(
        problem, None, deadline(), stage=ObjectiveStage(name="D_HUMAN", thermal_seed=wrong)
    )
    assert result.status == "MODEL_INVALID" and result.candidate is None


def test_local_search_reduces_only_the_objective_phases_and_leaves_the_cached_model_free():
    from ortools.sat.python import cp_model

    from app.domain.ids import CarrierId
    from app.scheduling.greedy import GreedyScheduler
    from app.scheduling.thermal_neighborhood import add_thermal_neighborhood

    knowledge, problem, _ = ft_case()
    alternative = problem.standalone_candidates[0].model_copy(
        update={"carrier_id": CarrierId("synthetic-thermal-alternative")}
    )
    problem = problem.model_copy(
        update={"standalone_candidates": (*problem.standalone_candidates, alternative)}
    )
    seed = GreedyScheduler().solve_serial(problem, deadline()).candidate
    assert ScheduleValidator().validate(knowledge, problem.runtime, problem, seed).valid
    native = CpSatScheduler()
    local = native._builder(problem, deadline())
    base = native._base_builder
    before = str(base.model.proto)
    assert len(base.human_intervals) == 5
    add_thermal_neighborhood(local, seed)
    assert len(local.human_intervals) == 4
    assert len(base.human_intervals) == 5
    heat_ids = {task.task_id for task in problem.logical_tasks if task.operation.action == "HEAT"}
    heat = next(a for a in seed.assignments if heat_ids.intersection(a.task_ids))
    local.model.add(local.carrier_starts[heat.carrier_id] != heat.interval.start_sec)
    assert cp_model.CpSolver().solve(local.model) == cp_model.INFEASIBLE
    free = native._builder(problem, deadline())
    free.model.add(free.carrier_starts[heat.carrier_id] >= heat.interval.start_sec + 1)
    assert cp_model.CpSolver().solve(free.model) == cp_model.OPTIMAL
    cold_ids = {task.task_id for task in problem.logical_tasks if task.operation.action == "WAIT"}
    cold = next(a for a in seed.assignments if cold_ids.intersection(a.task_ids))
    local = native._builder(problem, deadline())
    add_thermal_neighborhood(local, seed)
    local.model.add(local.carrier_starts[cold.carrier_id] >= cold.interval.start_sec + 1)
    assert cp_model.CpSolver().solve(local.model) == cp_model.OPTIMAL
    assert str(base.model.proto) == before
