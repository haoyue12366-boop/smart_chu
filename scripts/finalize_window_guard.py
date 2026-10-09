"""绑定本轮局部修复的真实证据；保留全部历史失败与质量代价。"""

import hashlib
import json
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

from app.config import ROOT
from benchmarks.runner import source_hashes, write_json
from scripts.run_time_save_regression import test_hashes

EVIDENCE = ROOT / "benchmarks/reports/verification/P6-dynamic-window-fix-run1"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ref(path):
    return {"path": path.relative_to(ROOT).as_posix(), "sha256": digest(path)}


def main():
    source = source_hashes()
    audits = json.loads((EVIDENCE / "paired-terminal-audit.json").read_bytes())
    assert audits["source_hashes"] == source
    assert len(audits["terminal_scans"]) == 120
    assert audits["historical_pairs"] == {"COMPLETED->COMPLETED": 55, "FAILED->COMPLETED": 65}
    assert audits["current_pairs"] == {"COMPLETED->COMPLETED": 56, "FAILED->COMPLETED": 64}
    assert audits["observed_replan_latency_ms"]["max"] <= 3000
    first = json.loads((EVIDENCE / "regression-final-run.json").read_bytes())
    assert (
        first["source_hashes"] == source and first["source_unchanged"] and first["tests_unchanged"]
    )
    current_test_hashes = test_hashes()
    changed = {n for n, h in first["test_hashes"].items() if current_test_hashes[n] != h}
    evidence_module = "tests.integration.test_robustness_dispatch_evidence"
    assert changed == {evidence_module.replace(".", "/") + ".py"}
    original = list(ET.parse(EVIDENCE / "regression-final.xml").getroot().iter("testcase"))
    replaced = [c for c in original if c.attrib["classname"] == evidence_module]
    assert len(replaced) == 1 and replaced[0].find("failure") is not None
    current = [c for c in original if c.attrib["classname"] != evidence_module]
    retried = list(ET.parse(EVIDENCE / "evidence-retry.xml").getroot().iter("testcase"))
    assert len(retried) == 2 and all(c.attrib["classname"] == evidence_module for c in retried)
    current.extend(retried)
    assert len(current) == 288
    assert all(
        c.find("failure") is None and c.find("error") is None and c.find("skipped") is None
        for c in current
    )
    baseline = json.loads((EVIDENCE / "baseline-source.json").read_bytes())["source_hashes"]
    changed_source = sorted(n for n, h in baseline.items() if source[n] != h)
    added_source = sorted(set(source) - set(baseline))
    assert changed_source == [
        "app/domain/policy.py",
        "app/runtime/simulator.py",
        "benchmarks/robustness/dispatch.py",
        "benchmarks/robustness/simulator.py",
    ]
    assert added_source == [
        "app/runtime/dispatch_guard.py",
        "app/runtime/dispatch_windows.py",
        "benchmarks/robustness/window_repair.py",
    ]
    assert all(
        source[n] == h
        for n, h in json.loads((EVIDENCE / "final-source.json").read_bytes())[
            "source_hashes"
        ].items()
    )
    assert all(digest(EVIDENCE / "final-source" / n) == h for n, h in source.items())
    for name in ("ruff-delivery-run2.log", "format-delivery-run2.log", "mypy-final.log"):
        text = (EVIDENCE / name).read_text(encoding="utf-8-sig")
        assert "passed" in text.lower() or "already formatted" in text or "no issues found" in text
    http_path = ROOT / "benchmarks/reports/P6-dynamic-window-http-run1/report.json"
    http = json.loads(http_path.read_bytes())
    assert (
        http["status"] == "PASSED" and http["source_hashes"] == source and http["source_unchanged"]
    )
    assert len(http["samples"]) == 36
    assert all(
        r["passed_correctness"] and r["service_elapsed_ms"] <= r["budget_ms"]
        for r in http["samples"]
    )
    for row in http["samples"]:
        assert digest(http_path.parent / row["artifact"]) == row["artifact_sha256"]
    windows_path = EVIDENCE / "windows-run2/report.json"
    windows = json.loads(windows_path.read_bytes())
    assert windows["status"] == "PASSED" and windows["source_hashes"] == source
    assert windows["source_unchanged"] and windows["failures"] == 0 and windows["skips"] == 0
    archive = ROOT / "data/delivery/P6-Windows-window-guard-v2.zip"
    assert digest(archive) == windows["delivery"]["archive_sha256"]
    delivery = json.loads(archive.with_suffix("").joinpath("release_manifest.json").read_bytes())
    assert delivery["policy"]["dispatch_guard_policy_id"] == "TIGHT_HUMAN_V1"
    assert delivery["policy"]["replan_budget"]["total_ms"] == 3000
    for name, sha in source.items():
        if name.startswith("app/"):
            assert delivery["files"][name] == sha
    result = {
        "status": "LOCAL_FIXED_120_RELIABILITY_VERIFIED_WITH_QUALITY_TRADEOFFS",
        "created_at": datetime.now(UTC).isoformat(),
        "formal_acceptance": False,
        "full_p6_acceptance": False,
        "full_experiment_omitted": True,
        "default_guard_enabled": False,
        "source_hashes": source,
        "production_files_changed": [
            n for n in changed_source + added_source if n.startswith("app/")
        ],
        "changed_source": changed_source,
        "added_source": added_source,
        "test_hashes": test_hashes(),
        "current_regression_passed": len(current),
        "external_gates_deselected": first["external_gates_deselected"],
        "regression_runs": [
            ref(EVIDENCE / n)
            for n in ("regression-final.xml", "regression-final-run.json", "evidence-retry.xml")
        ],
        "historical_failures_recovered": 65,
        "original_successes_retained": 55,
        "new_baseline_failures": 64,
        "remaining_failures_in_fixed_scope": 0,
        "independent_terminal_scans": 120,
        "infeasibility_claims": [],
        "quality_tradeoff": {
            "spread_under_300_count": audits["spread_under_300_count"],
            "sample_count": 120,
            "paired_quality": audits["quality_among_both_completed"],
            "interpretation": (
                "保守等待使所有原成功轨迹总用时增加；人工实际工作量不变。"
                "出菜差与连续工作块有好有坏，优秀指标未全面达标，默认关闭。"
            ),
        },
        "http_statistics": http["statistics"],
        "references": [
            ref(EVIDENCE / n)
            for n in (
                "historical-diagnosis.json",
                "paired-terminal-audit.json",
                "baseline-source.json",
                "final-source.json",
                "ruff-delivery-run2.log",
                "format-delivery-run2.log",
                "mypy-final.log",
            )
        ]
        + [ref(http_path), ref(windows_path)],
        "policy": ref(ROOT / "data/policies/p6-window-guard-v1.json"),
        "optional_windows_archive": ref(archive),
        "producer_sha256": digest(Path(__file__)),
    }
    write_json(EVIDENCE / "verification-summary.json", result)
    write_json(ROOT / "deploy/window_guard_fix_manifest_run1.json", result)
    print(
        json.dumps(
            {
                k: result[k]
                for k in (
                    "status",
                    "current_regression_passed",
                    "historical_failures_recovered",
                    "original_successes_retained",
                    "independent_terminal_scans",
                )
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
