"""历史窗口纠正证据绑定归档源码；当前工艺链保护另行绑定现版本。"""

import gzip
import hashlib
import json
from collections import Counter

from benchmarks.dataset import ROOT
from benchmarks.runner import source_hashes


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_fixed_initial_four_group_comparison_has_all_pairs_and_terminal_proofs():
    directory = ROOT / "benchmarks/reports/P6-robustness-window-compare-run2"
    report = json.loads((directory / "report.json").read_bytes())
    inventory = json.loads(
        (
            ROOT
            / (
                "benchmarks/reports/verification/P6-robustness-window-fix/small-artifact-inventory.json"
            )
        ).read_bytes()
    )
    assert digest(directory / "report.json") == inventory["report_sha256"]
    for name, expected in inventory["files"].items():
        assert digest(directory / name) == expected
    assert inventory["files"] == report["artifact_hashes"]
    assert report["status"] == "EXPERIMENT_COMPLETED"
    assert report["partial"] is True and report["formal_acceptance"] is False
    assert report["source_unchanged"] and report["original_report_unchanged"]
    assert report["initial_artifacts_unchanged"]
    assert report["expected_trajectories"] == report["actual_trajectories"] == 240
    assert report["case_ids"] == ["combination-000", "combination-018", "combination-052"]
    original = report["baseline_manifest"]["original_report"]
    assert digest(ROOT / original["path"]) == original["sha256"]
    for name in (
        "benchmarks/robustness/dispatch.py",
        "benchmarks/robustness/simulator.py",
        "benchmarks/robustness/runner.py",
        "benchmarks/robustness/compare_dispatch.py",
    ):
        archived = (
            ROOT / "benchmarks/reports/verification/P6-dynamic-window-fix-run1/before-source" / name
        )
        assert digest(archived) == report["source_hashes"][name]
    pairs = {}
    for row in report["rows"]:
        key = (row["case_id"], row["group"], row["trajectory"])
        artifact = json.loads(gzip.decompress((directory / row["artifact"]).read_bytes()))
        assert row["artifact"] in inventory["files"]
        assert artifact["status"] == row["status"]
        if row["status"] == "COMPLETED":
            assert artifact["validation"]["valid"]
            assert artifact["completed_task_count"] == artifact["required_task_count"]
        else:
            assert artifact["failure"]
            if row["simulator_version"] == "observed-start-window-v2":
                assert artifact["failure_detail"]["code"]
        pair = pairs.setdefault(key, {})
        assert row["simulator_version"] not in pair
        pair[row["simulator_version"]] = row
    assert len(pairs) == 120
    for pair in pairs.values():
        assert set(pair) == set(report["simulators"])
        assert len({r["initial_problem_hash"] for r in pair.values()}) == 1
        assert len({r["initial_candidate_hash"] for r in pair.values()}) == 1
    for version in report["simulators"]:
        for group in report["config"]["groups"]:
            selected = [
                r
                for r in report["rows"]
                if r["simulator_version"] == version and r["group"] == group
            ]
            summary = report["summaries"][version][group]
            completed = sum(r["status"] == "COMPLETED" for r in selected)
            assert len(selected) == summary["trajectory_count"] == 30
            assert summary["completed"] == summary["spread_denominator"] == completed
            assert summary["failed"] == 30 - completed
            assert summary["failure_rate"] == (30 - completed) / 30
    for group in report["config"]["groups"]:
        transitions = Counter(
            p["legacy-shift-v1"]["status"] + "->" + p["observed-start-window-v2"]["status"]
            for (case, selected_group, trajectory), p in pairs.items()
            if selected_group == group
        )
        assert dict(transitions) == report["paired_outcomes"][group]


def test_current_guard_fixed_120_evidence_covers_current_source_and_original_successes():
    directory = ROOT / "benchmarks/reports/P6-dynamic-window-after-run2"
    report = json.loads((directory / "report.json").read_bytes())
    historical = json.loads(
        (ROOT / "benchmarks/reports/P6-feedback-critical-small-run2/report.json").read_bytes()
    )
    assert report["source_hashes"] == source_hashes()
    assert report["source_unchanged"] and report["history_unchanged"]
    assert report["dispatch_guard_policy_id"] == "TIGHT_HUMAN_V1"
    assert report["status"] == "EXPERIMENT_COMPLETED"
    assert report["partial"] and not report["formal_acceptance"]
    pairs = {(r["case_id"], r["group"], r["trajectory"]): r for r in report["rows"]}
    assert len(pairs) == 120
    original = [
        r for r in historical["rows"] if (r["case_id"], r["group"], r["trajectory"]) in pairs
    ]
    assert sum(r["status"] == "COMPLETED" for r in original) == 55
    assert sum(r["status"] == "FAILED" for r in original) == 65
    for row in pairs.values():
        path = directory / row["artifact"]
        assert digest(path) == row["sha256"]
        frozen = json.loads(gzip.decompress(path.read_bytes()))
        assert frozen["status"] == row["status"] == "COMPLETED"
        assert frozen["validation"]["valid"]
        assert frozen["completed_task_count"] == frozen["required_task_count"]
