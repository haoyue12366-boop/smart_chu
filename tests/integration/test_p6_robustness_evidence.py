"""新七组全量证据逐条校验，原四组和修复前历史报告保持原身份。"""

import json
from collections import Counter

from benchmarks.dataset import ROOT
from benchmarks.robustness.feedback_runner import GROUPS
from tests.p6_evidence_support import (
    current_sources,
    inputs,
    read_artifact,
    report_for,
    same_suite,
    sha256,
)


def test_all_paired_trajectories_and_failed_denominators_are_preserved():
    path, report = report_for("robustness")
    inventory = json.loads((ROOT / inputs()["robustness_inventory"]).read_bytes())
    assert inventory["status"] == "PASSED"
    assert inventory["report_sha256"] == sha256(path)
    assert inventory["producer_sha256"] == sha256(ROOT / "scripts/audit_feedback_experiment.py")
    assert inventory["source_exclusions"] == []
    assert inventory["files"] == report["artifact_hashes"]
    assert report["status"] == "EXPERIMENT_COMPLETED" and report["partial"] is False
    assert report["actual_trajectories"] == report["expected_trajectories"] == 28000
    assert len(report["rows"]) == 28000 and len(report["initial_plans"]) == 160
    same_suite(report)
    assert report["base_policy_hash"] == inputs()["policy_hash"]
    assert report["buffered_enabled_by_default"] is False
    assert report["critical_enabled_by_default"] is False
    assert report["original_report_unchanged"] is True
    current_sources(
        report,
        "benchmarks/robustness/runner.py",
        "benchmarks/robustness/simulator.py",
        "benchmarks/robustness/observed_session.py",
        "benchmarks/robustness/dispatch.py",
        "benchmarks/robustness/feedback.py",
        "benchmarks/robustness/feedback_cache.py",
        "benchmarks/robustness/feedback_planner.py",
        "benchmarks/robustness/feedback_runner.py",
        "benchmarks/scenarios/duration_profiles.json",
    )
    assert len({r["case_id"] for r in report["rows"]}) == 40
    assert len({(r["case_id"], r["group"], r["trajectory"]) for r in report["rows"]}) == 28000
    assert Counter(r["group"] for r in report["rows"]) == {group: 4000 for group in GROUPS}
    for name, digest in inventory["files"].items():
        assert sha256(path.parent / name) == digest, "归档被改写：" + name
    for group, summary in report["summaries"].items():
        rows = [r for r in report["rows"] if r["group"] == group]
        completed = sum(r["status"] == "COMPLETED" for r in rows)
        assert summary["trajectory_count"] == completed + summary["failed"] == 4000
        assert summary["spread_denominator"] == summary["completed"] == completed
        assert summary["incomplete_separately_counted"] == summary["failed"]
        assert summary["failure_rate"] == summary["failed"] / 4000
        assert sum(summary["failure_codes"].values()) == summary["failed"]
    for row in report["rows"]:
        assert 0 <= row["trajectory"] < 100
        if not row["group"].endswith("CONTINUOUS"):
            assert row["replan_count"] <= 1
        frozen = read_artifact(path.parent, row["artifact"])
        assert frozen["status"] == row["status"] and frozen["group"] == row["group"]
        if row["status"] == "COMPLETED":
            assert frozen["validation"]["valid"]
            assert frozen["completed_task_count"] == frozen["required_task_count"]
        else:
            assert frozen["failure"] and frozen["failure_detail"]["code"]
    assert len(report["original_initial_artifacts"]) == 80
    original = ROOT / report["baseline_report"]["path"]
    assert sha256(original) == report["baseline_report"]["sha256"]
    for name, digest in report["original_initial_artifacts"].items():
        assert sha256(original.parent / name) == sha256(path.parent / name) == digest
    for case in {r["case_id"] for r in report["rows"]}:
        nominal = read_artifact(path.parent, f"initial-{case}-NOMINAL.json.gz")
        fast = read_artifact(path.parent, f"initial-{case}-NOMINAL_FAST.json.gz")
        if nominal["result"]["candidate"] is not None:
            assert (
                nominal["result"]["candidate"]["assignments"]
                == (fast["result"]["candidate"]["assignments"])
            )
        else:
            assert fast["result"]["candidate"] is None
    for pair in report["pair_transitions"].values():
        assert sum(pair.values()) == 4000


def test_pre_fix_report_remains_immutable_and_cannot_cover_current_simulator():
    original = ROOT / "benchmarks/reports/P6-robustness-run2/report.json"
    assert sha256(original) == "2baabc221737e4009339af86879ed56c8e3720bf3118eb9da721e693ce2e00da"
    historical = json.loads(original.read_bytes())
    name = "benchmarks/robustness/simulator.py"
    assert historical["source_hashes"][name] != sha256(ROOT / name)
