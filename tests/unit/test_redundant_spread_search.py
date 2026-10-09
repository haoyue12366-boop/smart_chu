"""已证明的阶段最优值只省重复搜索；独立合法候选和后续质量搜索仍必需。"""

import pytest

from app.domain.reports import SolveStatus
from app.scheduling.cp_sat import CpSatScheduler
from app.scheduling.engine import PlanningEngine
from app.validation.schedule import ScheduleValidator
from tests.unit.test_cp_sat_model import deadline
from tests.unit.test_finish_spread_bounds import capacity_oracle, terminal_menu


class RecordingSolver:
    def __init__(self, *, status=SolveStatus.OPTIMAL, bound=3, invalid=False):
        self.solver = CpSatScheduler()
        self.stages = []
        self.status, self.bound, self.invalid = status, bound, invalid

    def solve(self, problem, hint, limit, **kwargs):
        stage = kwargs["stage"]
        self.stages.append(stage)
        result = self.solver.solve(problem, hint, limit, **kwargs)
        if stage.name == "B_SPREAD" and sum(s.name == "B_SPREAD" for s in self.stages) == 1:
            assert result.status == "OPTIMAL" and result.objective_value == 3
            result = result.model_copy(update={"status": self.status, "best_bound": self.bound})
            if self.invalid:
                result = result.model_copy(
                    update={
                        "candidate": result.candidate.model_copy(
                            update={
                                "assignments": result.candidate.assignments[:-1],
                                "metrics": None,
                            }
                        )
                    }
                )
        return result


def run(solver):
    knowledge, problem = terminal_menu()
    problem = problem.model_copy(
        update={"policy": problem.policy.model_copy(update={"quality_first": True})}
    )
    result = PlanningEngine(validator=ScheduleValidator(), solver=solver).plan(
        problem, knowledge, problem.runtime, deadline()
    )
    return knowledge, problem, result


def test_proven_spread_optimum_avoids_redundant_search_and_still_optimizes_quality():
    solver = RecordingSolver()
    knowledge, problem, result = run(solver)
    assert result.status == "VALIDATED", result.failure
    assert sum(s.name == "B_SPREAD" for s in solver.stages) == 1
    assert any(s.name == "E_QUALITY" for s in solver.stages)
    assert result.candidate.metrics.cooking_finish_spread_sec == capacity_oracle(
        (5, 4, 3), (2, 1, 1), 3
    )
    assert ScheduleValidator().validate(knowledge, problem.runtime, problem, result.candidate).valid
    assert any(
        r.objective_stage == "E_QUALITY" and r.status == "OPTIMAL" for r in result.stage_results
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"bound": None},
        {"bound": 0},
        {"status": SolveStatus.FEASIBLE},
        {"invalid": True},
    ],
)
def test_missing_proof_or_rejected_candidate_keeps_followup_search(kwargs):
    solver = RecordingSolver(**kwargs)
    knowledge, problem, result = run(solver)
    assert result.status == "VALIDATED", result.failure
    assert sum(s.name == "B_SPREAD" for s in solver.stages) == 2
    assert any(s.name == "E_QUALITY" for s in solver.stages)
    assert ScheduleValidator().validate(knowledge, problem.runtime, problem, result.candidate).valid
    if kwargs.get("invalid"):
        assert result.rejected_candidates


def test_proven_spread_cannot_skip_search_when_new_flow_cap_excludes_seed(monkeypatch):
    # 显式合成的不可行总流程界；不能拿旧界下的合法解绕过更紧界。
    monkeypatch.setattr("app.scheduling.engine.makespan_cap", lambda *args: 0)
    solver = RecordingSolver()
    _, _, result = run(solver)
    assert sum(s.name == "B_SPREAD" for s in solver.stages) == 2
    assert result.status == "FAILED"
