"""按用户取消全量后的固定 210 条配对证据；严格保持部分实验身份。"""

import json
from collections import Counter

from benchmarks.dataset import ROOT
from benchmarks.robustness.feedback_runner import GROUPS
from benchmarks.runner import source_hashes
from tests.p6_evidence_support import read_artifact, sha256


def test_current_partial_experiment_preserves_all_pairs_and_independent_terminal_proofs():
    directory = ROOT / "benchmarks/reports/P6-feedback-critical-small-run2"
    path = directory / "report.json"
    report = json.loads(path.read_bytes())
    audit_path = ROOT / "benchmarks/reports/verification/P6-feedback-critical-fix/small2-audit.json"
    audit = json.loads(audit_path.read_bytes())
    assert report["status"] == "EXPERIMENT_COMPLETED"
    assert report["partial"] is True and report["formal_acceptance"] is False
    assert report["source_unchanged"] and report["source_hashes"] == source_hashes()
    assert audit["status"] == "PASSED" and audit["report_sha256"] == sha256(path)
    assert audit["producer_sha256"] == sha256(ROOT / "scripts/audit_feedback_experiment.py")
    assert audit["source_exclusions"] == []
    assert report["actual_trajectories"] == report["expected_trajectories"] == 210
    assert len(report["rows"]) == 210 and len(report["initial_plans"]) == 12
    assert {r["case_id"] for r in report["rows"]} == {
        "combination-000",
        "combination-018",
        "combination-052",
    }
    assert Counter(r["group"] for r in report["rows"]) == {g: 30 for g in GROUPS}
    assert len({(r["case_id"], r["group"], r["trajectory"]) for r in report["rows"]}) == 210
    assert report["config"]["seed"] == 20261004
    assert report["config"]["feedback_extension_sec"] == 30
    assert report["critical_enabled_by_default"] is False
    assert audit["files"] == report["artifact_hashes"]
    for name, digest in audit["files"].items():
        assert sha256(directory / name) == digest
    for row in report["rows"]:
        frozen = read_artifact(directory, row["artifact"])
        assert (frozen["status"], frozen["case_id"], frozen["group"], frozen["trajectory"]) == (
            row["status"],
            row["case_id"],
            row["group"],
            row["trajectory"],
        )
        if row["status"] == "COMPLETED":
            assert frozen["validation"]["valid"]
            assert frozen["completed_task_count"] == frozen["required_task_count"]
        else:
            assert frozen["failure"] and frozen["failure_detail"]["code"]
    assert all(
        sum(transitions.values()) == 30 for transitions in report["pair_transitions"].values()
    )
    original = ROOT / report["baseline_report"]["path"]
    assert report["original_report_unchanged"]
    assert sha256(original) == report["baseline_report"]["sha256"]
    assert len(report["original_initial_artifacts"]) == 6
    for name, digest in report["original_initial_artifacts"].items():
        assert sha256(directory / name) == sha256(original.parent / name) == digest
