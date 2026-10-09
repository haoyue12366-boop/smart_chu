"""当前质量证据必须包含全部固定请求，严格绑定源码、策略及原始计划。"""

import gzip
import hashlib
import json
from collections import Counter
from pathlib import Path

from app.config import ROOT, AppSettings
from app.domain.base import content_hash
from app.domain.policy import SchedulingPolicy
from app.domain.schedule import CandidateSchedule
from benchmarks.runner import source_hashes


def test_final_quality_http_evidence_binds_current_source_and_all_fixed_requests():
    assert AppSettings().policy_path.name == "p6-quality-v2.json"
    assert_fixed_quality_evidence("after-enabled-v21", "p6-quality-v2.json", 5000)


def test_saved_candidate_quality_evidence_binds_archived_source_and_all_fixed_requests():
    # 旧候选绑定其原始源码；新的默认策略必须另做完整 HTTP 验收。
    evidence = ROOT / "benchmarks/reports/2026-10-07-quality-fix"
    index = json.loads((evidence / "final-evidence-v20.json").read_bytes())
    original = evidence / "candidate-5s-expanded-v20/report.json"
    assert (
        hashlib.sha256(original.read_bytes()).hexdigest()
        == index["linked_evidence_sha256"]["candidate-5s-expanded-v20/report.json"]
    )
    assert_fixed_quality_evidence(
        "candidate-5s-expanded-v20",
        "p6-quality-v3-candidate.json",
        5000,
        source_archive=ROOT / index["archive"],
        require_all_initial_excellent=True,
    )


def assert_fixed_quality_evidence(
    output_name: str,
    policy_file: str,
    replan_budget_ms: int,
    *,
    source_archive: Path | None = None,
    require_all_initial_excellent: bool = False,
):
    output = ROOT / "benchmarks/reports/2026-10-07-quality-fix" / output_name
    report = json.loads((output / "report.json").read_bytes())
    assert report["status"] == "CORRECT_AND_WITHIN_BUDGET"
    assert (
        report["source_unchanged"] and report["require_five_minute"] and report["all_five_minute"]
    )
    assert report["formal_acceptance"] is False
    measured_source = (
        source_hashes()
        if source_archive is None
        else {
            relative: hashlib.sha256((source_archive / relative).read_bytes()).hexdigest()
            for relative in report["source_hashes"]
        }
    )
    assert report["source_hashes"] == measured_source
    policy = SchedulingPolicy.model_validate_json(
        (ROOT / "data/policies" / policy_file).read_bytes()
    )
    assert report["policy"] == policy.model_dump(mode="json")
    assert report["policy_hash"] == content_hash(policy)
    assert policy.quality_first and policy.objective.spread_target_sec == 300
    assert policy.initial_budget.total_ms == 7000
    assert policy.replan_budget.total_ms == replan_budget_ms
    profile = json.loads((ROOT / "benchmarks/scenarios/performance_profiles.json").read_bytes())
    suite = json.loads((ROOT / "benchmarks/scenarios/full_suite.json").read_bytes())
    cases = set(profile["case_ids"])
    replans = {c["case_id"] for c in suite["replans"]} & cases
    expected = {
        (round_number, case, stage)
        for round_number in (1, 2, 3)
        for case in cases
        for stage in (("INITIAL", "REPLAN") if case in replans else ("INITIAL",))
    }
    rows = report["samples"]
    identities = [(r["round"], r["case_id"], r["stage"]) for r in rows]
    assert len(rows) == len(set(identities)) == report["expected_requests"] == 153
    assert set(identities) == expected
    assert Counter(r["stage"] for r in rows) == {"INITIAL": 93, "REPLAN": 60}
    assert Counter(r["round"] for r in rows) == {1: 51, 2: 51, 3: 51}
    for row in rows:
        assert row["passed_correctness"] and row["status"] == "PUBLISHED"
        assert row["http_status"] == 200 and row["failure_reason"] is None
        budget = 7000 if row["stage"] == "INITIAL" else replan_budget_ms
        assert row["budget_ms"] == budget
        assert row["client_elapsed_ms"] <= budget and row["service_elapsed_ms"] <= budget
        # 用户允许初排最迟7秒；5秒是优秀等级，不能混作所有初排的硬门槛。
        if row["stage"] == "INITIAL" and require_all_initial_excellent:
            assert row["client_elapsed_ms"] <= 5000
        assert row["metrics"]["completion_spread_sec"] <= 300
        path = output / row["artifact"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == row["artifact_sha256"]
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            artifact = json.load(stream)
        assert artifact["proof"]["valid"] and not artifact["proof"]["violations"]
        candidate = CandidateSchedule.model_validate(artifact["plan"]["validated"]["candidate"])
        assert candidate.problem_hash == row["problem_hash"] == artifact["proof"]["problem_hash"]
        assert (
            candidate.candidate_hash == row["candidate_hash"] == artifact["proof"]["candidate_hash"]
        )
        assert candidate.metrics.model_dump(mode="json") == row["metrics"]
        assert set(artifact["competition_response"]) == {
            "overview",
            "cookingTimeline",
            "ingredientsSummary",
            "detailTimeline",
            "recipeDetail",
        }
        assert artifact["measurement"] == {
            k: v for k, v in row.items() if k not in {"artifact", "artifact_sha256"}
        }
