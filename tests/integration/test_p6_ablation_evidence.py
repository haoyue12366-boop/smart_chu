"""真实受控对照含所有轮次和失败；图存储不改变数学问题身份。"""

from collections import Counter

from app.domain.schedule import CandidateSchedule
from app.domain.scheduling_problem import SchedulingProblem
from tests.p6_evidence_support import current_sources, read_artifact, report_for, same_suite


def test_complete_current_controlled_ablation_keeps_all_groups_and_trials():
    path, report = report_for("ablation")
    assert report["status"] == "EXPERIMENT_COMPLETED"
    assert len(report["rows"]) == 240
    same_suite(report)
    current_sources(report, "benchmarks/ablation.py", "benchmarks/scenarios/ablation_policies.json")
    groups = {g["id"] for g in report["config"]["groups"]}
    assert len(groups) == 10
    assert Counter(row["group"] for row in report["rows"]) == {group: 24 for group in groups}
    assert len({row["case_id"] for row in report["rows"]}) == 8
    comparison = report["graph_json"]
    assert comparison["status"] == "PASSED" and comparison["semantic_equal"]
    assert comparison["database_changes_mathematical_optimum_claimed"] is False
    assert len(comparison["rows"]) == 16
    for case_id in {row["case_id"] for row in comparison["rows"]}:
        pair = [row for row in comparison["rows"] if row["case_id"] == case_id]
        assert {row["origin"] for row in pair} == {"GRAPH", "JSON"}
        assert len({row["problem_hash"] for row in pair}) == 1
    for row in report["rows"]:
        frozen = read_artifact(path.parent, row["artifact"])
        assert frozen["summary"]["status"] == row["status"]
        if row["status"] == "VALIDATED":
            problem = SchedulingProblem.model_validate(frozen["problem"])
            candidate = CandidateSchedule.model_validate(frozen["candidate"])
            proof = frozen["validation"]
            assert proof["valid"]
            assert problem.problem_hash == candidate.problem_hash == proof["problem_hash"]
            assert candidate.candidate_hash == proof["candidate_hash"]
        else:
            assert row["failure"]
    for group, result in report["by_group"].items():
        actual = [r for r in report["rows"] if r["group"] == group]
        assert result["attempts"] == result["valid"] + result["failures"] == len(actual)
        assert result["valid"] == sum(r["status"] == "VALIDATED" for r in actual)
