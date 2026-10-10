"""前置阶段只求达标可行解；完整质量搜索与独立核验仍然执行。"""

import pytest
from ortools.sat.python import cp_model

from app.domain.objectives import ObjectiveStage
from app.domain.reports import SolveResult
from app.scheduling.cp_sat import CpSatScheduler
from app.scheduling.engine import PlanningEngine
from app.validation.schedule import ScheduleValidator
from tests.unit.test_cp_sat_model import deadline
from tests.unit.test_prepared_greedy import prepared_menu


def quality_case(at=0):
    knowledge, problem = prepared_menu(at)
    objective = problem.policy.objective.model_copy(
        update={"stages": ("SPREAD", "HUMAN_BUSY", "MAKESPAN"), "spread_target_sec": 300}
    )
    return knowledge, problem.model_copy(
        update={"policy": problem.policy.model_copy(update={"objective": objective})}
    )


@pytest.mark.parametrize("at", [0, 90])
def test_verified_feasibility_seed_is_followed_by_complete_quality_search(at):
    knowledge, problem = quality_case(at)
    original = problem.model_dump_json()
    stages = []
    solver = CpSatScheduler()

    class RecordingSolver:
        def solve(self, problem, hint, limit, *, stage=None, serial_menu=False):
            stages.append(stage)
            return solver.solve(problem, hint, limit, stage=stage, serial_menu=serial_menu)

    result = PlanningEngine(validator=ScheduleValidator(), solver=RecordingSolver()).plan(
        problem, knowledge, problem.runtime, deadline()
    )
    assert result.status == "VALIDATED", result.failure
    parallel = [stage for stage in stages if stage is not None]
    assert parallel[0].name == "B_SPREAD"
    assert parallel[0].spread_excess_cap_sec == 0
    assert parallel[-1].name == "E_QUALITY"
    assert parallel[-1].human_busy_cap_sec is None
    assert parallel[-1].total_human_cap_sec is None
    assert problem.model_dump_json() == original
    for candidate in (result.candidate, result.serial_reference_candidate):
        assert ScheduleValidator().validate(knowledge, problem.runtime, problem, candidate).valid
    # 独立下界：每道菜必需60秒人工加热；要把最长块压到60秒，
    # 第二次加热至少在第一次结束后休息60秒，再冷却1200秒、收尾30秒。
    # 0/120秒加热、1260/1380秒收尾给出可达到的1410秒见证。
    assert result.candidate.metrics.max_continuous_human_sec == 60
    assert result.candidate.metrics.makespan_sec == at + 1410
    assert result.human_objective_optimized


@pytest.mark.parametrize(
    ("stage", "expected"),
    [
        (ObjectiveStage(name="B_SPREAD", spread_excess_cap_sec=0), (0, 0)),
        (ObjectiveStage(name="B_SPREAD"), (2, 1)),
        (ObjectiveStage(name="C_MAKESPAN", spread_excess_cap_sec=0), (2, 1)),
    ],
)
def test_only_constant_spread_feasibility_search_uses_lightweight_parameters(
    monkeypatch, stage, expected
):
    _, problem = quality_case()
    parameters = []
    original = cp_model.CpSolver

    class RecordingNativeSolver(original):
        def solve(self, model, solution_callback=None):
            parameters.append(
                (self.parameters.cp_model_probing_level, self.parameters.linearization_level)
            )
            return super().solve(model, solution_callback)

    monkeypatch.setattr(cp_model, "CpSolver", RecordingNativeSolver)
    result = CpSatScheduler().solve(problem, None, deadline(), stage=stage)
    assert result.status == "OPTIMAL", result
    assert parameters == [expected]
    if stage.name == "B_SPREAD":
        assert result.objective_value == result.best_bound == 0


def test_zero_spread_seed_still_rejects_an_impossible_human_window():
    _, problem = quality_case()
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
    # 两个60秒主动段必须在[0,60)内完成，单人容量给出独立不可行证明。
    result = CpSatScheduler().solve(
        problem,
        None,
        deadline(),
        stage=ObjectiveStage(name="B_SPREAD", spread_excess_cap_sec=0),
    )
    assert result.status == "INFEASIBLE" and result.candidate is None


def test_invalid_seed_is_rejected_and_unknown_quality_preserves_a_valid_fallback():
    knowledge, problem = quality_case()
    solver = CpSatScheduler()
    invalid = []

    class InvalidSeedSolver:
        def solve(self, problem, hint, limit, *, stage=None, serial_menu=False):
            if stage and stage.name == "E_QUALITY":
                return SolveResult(status="UNKNOWN", problem_hash=problem.problem_hash)
            result = solver.solve(problem, hint, limit, stage=stage, serial_menu=serial_menu)
            if stage and stage.name == "B_SPREAD" and result.candidate is not None:
                human = [a for a in result.candidate.assignments if a.resource_uses]
                first, second = sorted(human, key=lambda a: a.interval.start_sec)[:2]
                forged = result.candidate.model_copy(
                    update={
                        "assignments": tuple(
                            a.model_copy(update={"interval": first.interval})
                            if a.carrier_id == second.carrier_id
                            else a
                            for a in result.candidate.assignments
                        ),
                        "metrics": None,
                    }
                )
                invalid.append(forged)
                return result.model_copy(update={"candidate": forged})
            return result

    result = PlanningEngine(validator=ScheduleValidator(), solver=InvalidSeedSolver()).plan(
        problem, knowledge, problem.runtime, deadline()
    )
    assert invalid and result.status == "VALIDATED", result.failure
    assert result.rejected_candidates
    assert not result.human_objective_optimized
    assert all(
        not ScheduleValidator().validate(knowledge, problem.runtime, problem, candidate).valid
        for candidate in invalid
    )
    assert ScheduleValidator().validate(knowledge, problem.runtime, problem, result.candidate).valid
