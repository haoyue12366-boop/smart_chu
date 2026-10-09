"""全量产物检查；部分诊断或不完整计划不能代替固定 340 场景。"""

from app.domain.schedule import CandidateSchedule
from app.domain.scheduling_problem import SchedulingProblem
from tests.p6_evidence_support import current_sources, read_artifact, report_for, same_suite


def test_current_complete_suite_keeps_all_cases_and_independent_proofs():
    path, report = report_for("full")
    assert report["status"] == "PASSED" and report["scope"] == "FULL_DEVELOPMENT"
    assert report["attempted_cases"] == 340 and report["failure_count"] == 0
    assert len(report["case_results"]) == 340 and all(c["passed"] for c in report["case_results"])
    assert len(report["samples"]) == 365
    same_suite(report)
    current_sources(report, "benchmarks/runner.py", "benchmarks/dataset.py")
    for row in report["samples"]:
        assert row["status"] in {"PUBLISHED", "NO_REPLAN"}
        assert row["constraint_validation"] and row["interface_validation"]
        frozen = read_artifact(path.parent, row["artifact_path"], row["artifact_sha256"])
        assert frozen["proof"]["valid"]
        assert frozen["proof"]["problem_hash"] == row["problem_hash"]
        problem = SchedulingProblem.model_validate(frozen["problem"])
        candidate = CandidateSchedule.model_validate(
            frozen["result"]["plan"]["validated"]["candidate"]
        )
        assert problem.problem_hash == candidate.problem_hash == row["problem_hash"]
        assert candidate.candidate_hash == frozen["proof"]["candidate_hash"]
        assert set(frozen["response"]) == {
            "overview",
            "cookingTimeline",
            "ingredientsSummary",
            "detailTimeline",
            "recipeDetail",
        }
