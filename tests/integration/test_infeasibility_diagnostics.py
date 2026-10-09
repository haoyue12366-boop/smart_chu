"""离线诊断只解释得到证明的模型；合成冲突不冒充真实菜谱工艺。"""

import pytest

from app.domain.objectives import ObjectiveStage
from app.scheduling.cp_sat import CpSatScheduler
from app.scheduling.diagnostics import InfeasibilityAnalyzer
from tests.unit.test_cp_sat_model import deadline
from tests.validator_support import resource_example


def build(problem, stage=None):
    solver = CpSatScheduler()
    result = solver.solve(problem, None, deadline(), stage=stage)
    return result, solver.last_build_report


def test_unknown_does_not_claim_cause_or_run_deep_search():
    _, _, problem, _ = resource_example()
    _, report = build(problem)
    report = report.model_copy(update={"solve_status": "UNKNOWN"})
    result = InfeasibilityAnalyzer().diagnose(problem, report, report.objective_stage, deadline())
    assert result.explanation_status == "INCOMPLETE"
    assert not result.conflict_constraint_ids
    assert result.base_model_feasibility == "UNKNOWN"
    assert result.shrink_checks == 0


def test_impossible_objective_bound_does_not_accuse_feasible_base_recipe():
    _, _, problem, _ = resource_example()
    result, report = build(problem, ObjectiveStage(name="B_SPREAD", makespan_cap_sec=1))
    assert result.status == "INFEASIBLE"
    diagnosis = InfeasibilityAnalyzer().diagnose(
        problem, report, report.objective_stage, deadline()
    )
    assert diagnosis.scope == "OBJECTIVE_STAGE"
    assert diagnosis.base_model_feasibility == "FEASIBLE"
    assert diagnosis.explanation_status == "PROVEN_CONFLICT"
    assert diagnosis.solver_build_id == report.solver_build_id
    assert diagnosis.diagnostic_build_id != report.solver_build_id
    assert not diagnosis.minimality_proven
    assert diagnosis.shrink_checks <= 6
    assert diagnosis.search_workers == 1
    assert diagnosis.elapsed_ms <= 2100


def test_resource_window_conflict_has_traceable_constraints_and_no_minimal_claim():
    _, _, problem, _ = resource_example()
    problem = problem.model_copy(
        update={
            "logical_tasks": tuple(
                t.model_copy(update={"latest_end_sec": 120}) for t in problem.logical_tasks
            )
        }
    )
    result, report = build(problem)
    assert result.status == "INFEASIBLE"
    diagnosis = InfeasibilityAnalyzer().diagnose(
        problem, report, report.objective_stage, deadline()
    )
    assert diagnosis.base_model_feasibility == "INFEASIBLE"
    assert diagnosis.explanation_status == "PROVEN_CONFLICT"
    assert diagnosis.conflict_constraint_ids
    assert set(diagnosis.conflict_constraint_ids) <= {
        r.constraint_id for r in problem.constraint_catalog
    }
    assert not diagnosis.minimality_proven
    assert diagnosis.uncontrolled_constraint_count > 0


def test_diagnostic_rejects_cross_build_identity_and_honors_expired_deadline():
    _, _, problem, _ = resource_example()
    _, report = build(problem)
    analyzer = InfeasibilityAnalyzer()
    with pytest.raises(ValueError, match="身份"):
        analyzer.diagnose(
            problem,
            report.model_copy(update={"problem_hash": "f" * 64}),
            report.objective_stage,
            deadline(),
        )
    report = report.model_copy(update={"solve_status": "INFEASIBLE"})
    result = analyzer.diagnose(problem, report, report.objective_stage, deadline(0))
    assert not result.diagnosis_complete
    assert result.explanation_status == "INCOMPLETE"
