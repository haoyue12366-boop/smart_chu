"""顺序执行修复后复验；每个真实命令保存退出码、耗时与日志，失败即停。"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from benchmarks.dataset import ROOT
from benchmarks.runner import source_hashes, write_json

RELATED = (
    "tests/unit/test_feedback_cache.py",
    "tests/unit/test_robustness_windows.py",
    "tests/integration/test_robustness_dispatch.py",
    "tests/integration/test_robustness_archive_replay.py",
    "tests/integration/test_robustness_dispatch_evidence.py",
    "tests/integration/test_robustness_no_future_leak.py",
    "tests/unit/test_duration_policy.py",
    "tests/integration/test_robustness_feedback.py",
    "tests/integration/test_critical_window_policy.py",
    "tests/integration/test_fast_feedback_planning.py",
    "tests/integration/test_fast_feedback_service.py",
)


def run(output: Path, container: str, without_robustness: bool = False) -> int:
    output.mkdir(parents=True, exist_ok=False)
    environment = {
        **os.environ,
        "PYTHONUTF8": "1",
        "SMART_COOKING_TEST_RELEASE_ROOT": str(ROOT / ".tools/p2-regression/releases"),
        "SMART_COOKING_NEO4J_TEST": "1",
    }
    python = sys.executable
    npm = shutil.which("npm.cmd")
    if npm is None:
        raise RuntimeError("锁定的 Windows npm 不可用")
    stages = [
        (
            "small-audit",
            [
                python,
                "-m",
                "scripts.audit_feedback_experiment",
                "--directory",
                "benchmarks/reports/P6-feedback-critical-small-run2",
                "--output",
                "benchmarks/reports/verification/P6-feedback-critical-fix/small2-audit.json",
            ],
            ROOT,
        ),
        ("ruff", [python, "-m", "ruff", "check", "."], ROOT),
        ("format", [python, "-m", "ruff", "format", "--check", "."], ROOT),
        ("mypy", [python, "-m", "mypy", "app"], ROOT),
        (
            "related-tests",
            [
                python,
                "-m",
                "pytest",
                *RELATED,
                "-q",
                "-p",
                "no:cacheprovider",
                "--junitxml",
                str(output / "related-tests.xml"),
            ],
            ROOT,
        ),
        ("frontend-types", [npm, "run", "typecheck"], ROOT / "web"),
        ("frontend-tests", [npm, "run", "test:unit"], ROOT / "web"),
        ("frontend-build", [npm, "run", "build"], ROOT / "web"),
        (
            "full-340",
            [
                python,
                "-m",
                "benchmarks.runner",
                "--output",
                "benchmarks/reports/P6-full-feedback-run1",
            ],
            ROOT,
        ),
        (
            "ablation-240",
            [
                python,
                "-m",
                "benchmarks.ablation",
                "--output",
                "benchmarks/reports/P6-ablation-feedback-run1",
            ],
            ROOT,
        ),
        (
            "properties",
            [
                python,
                "-m",
                "pytest",
                "tests/property",
                "tests/stateful",
                "-q",
                "-p",
                "no:cacheprovider",
                "--junitxml",
                "data/verification/P6-properties-feedback-run1.xml",
            ],
            ROOT,
        ),
        (
            "property-summary",
            [
                python,
                "-m",
                "benchmarks.property_profiles",
                "--xml",
                "data/verification/P6-properties-feedback-run1.xml",
                "--output",
                "data/verification/P6-property-summary-feedback-run1.json",
            ],
            ROOT,
        ),
        (
            "faults",
            [
                python,
                "-m",
                "benchmarks.fault_injection",
                "--output",
                "benchmarks/reports/P6-faults-feedback-run1",
                "--neo4j-container",
                container,
            ],
            ROOT,
        ),
        (
            "performance-153",
            [
                python,
                "-m",
                "benchmarks.performance",
                "--output",
                "benchmarks/reports/P6-performance-feedback-run1",
            ],
            ROOT,
        ),
        (
            "windows-package",
            [
                python,
                "-m",
                "scripts.package_release",
                "--output",
                "data/delivery/P6-Windows-feedback-run1",
                "--wheelhouse",
                "data/delivery/P6-Windows-technical-run1/wheels",
                "--requirements",
                "data/delivery/P6-Windows-technical-run1/requirements.txt",
            ],
            ROOT,
        ),
        (
            "windows-smoke",
            [
                python,
                "-m",
                "scripts.verify_windows_delivery",
                "--output",
                "data/verification/P6-windows-feedback-run1",
                "--archive",
                "data/delivery/P6-Windows-feedback-run1.zip",
                "--neo4j-container",
                container,
            ],
            ROOT,
        ),
        (
            "robustness-28000",
            [
                python,
                "-m",
                "benchmarks.robustness.feedback_runner",
                "--output",
                "benchmarks/reports/P6-feedback-critical-full-run1",
            ],
            ROOT,
        ),
        (
            "robustness-audit",
            [
                python,
                "-m",
                "scripts.audit_feedback_experiment",
                "--directory",
                "benchmarks/reports/P6-feedback-critical-full-run1",
                "--output",
                "data/verification/P6-feedback-critical-full-audit-run1.json",
            ],
            ROOT,
        ),
    ]
    if without_robustness:
        omitted = {"full-340", "ablation-240", "robustness-28000", "robustness-audit"}
        stages = [stage for stage in stages if stage[0] not in omitted]
    report = {
        "status": "RUNNING",
        "formal_acceptance": False,
        "started_at": datetime.now(UTC).isoformat(),
        "source_hashes": source_hashes(),
        "full_robustness_included": not without_robustness,
        "user_scope_quote": "不用全量" if without_robustness else None,
        "stages": [],
    }
    write_json(output / "report.json", report)
    for name, command, cwd in stages:
        began = time.perf_counter_ns()
        row = {
            "name": name,
            "status": "RUNNING",
            "command": command,
            "cwd": str(cwd),
            "started_at": datetime.now(UTC).isoformat(),
            "log": name + ".log",
        }
        report["stages"].append(row)
        write_json(output / "report.json", report)
        print(json.dumps({"stage": name, "status": "RUNNING"}), flush=True)
        with (output / row["log"]).open("w", encoding="utf-8") as log:
            completed = subprocess.run(
                command, cwd=cwd, env=environment, stdout=log, stderr=subprocess.STDOUT
            )
        row.update(
            exit_code=completed.returncode,
            elapsed_ms=(time.perf_counter_ns() - began) / 1_000_000,
            finished_at=datetime.now(UTC).isoformat(),
            status="PASSED" if completed.returncode == 0 else "FAILED",
        )
        print(
            json.dumps({"stage": name, "status": row["status"], "elapsed_ms": row["elapsed_ms"]}),
            flush=True,
        )
        if completed.returncode != 0:
            report["status"] = "FAILED"
            break
        write_json(output / "report.json", report)
    else:
        report["status"] = (
            "LIMITED_REVALIDATION_COMPLETED_FULL_OMITTED_BY_USER"
            if without_robustness
            else "RUNS_COMPLETED_AWAITING_AGGREGATE_GATE"
        )
    report["source_unchanged"] = report["source_hashes"] == source_hashes()
    report["finished_at"] = datetime.now(UTC).isoformat()
    if not report["source_unchanged"]:
        report["status"] = "INVALID_SOURCE_CHANGED"
    write_json(output / "report.json", report)
    return (
        0
        if report["status"]
        in {
            "LIMITED_REVALIDATION_COMPLETED_FULL_OMITTED_BY_USER",
            "RUNS_COMPLETED_AWAITING_AGGREGATE_GATE",
        }
        else 1
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--neo4j-container", required=True)
    parser.add_argument("--after-report", type=Path)
    parser.add_argument("--without-robustness", action="store_true")
    args = parser.parse_args()
    if args.after_report:
        print(json.dumps({"waiting_for": str(args.after_report)}), flush=True)
        while True:
            try:
                previous = json.loads(args.after_report.read_bytes())
            except json.JSONDecodeError:
                # 报告文件刚被写入时可能短暂未完整；不将半个 JSON 当实验失败。
                time.sleep(1)
                continue
            if previous["status"] != "RUNNING":
                break
            time.sleep(5)
        if previous["status"] != "EXPERIMENT_COMPLETED":
            raise RuntimeError("前序配对实验未完成，拒绝启动下一轮")
    return run(args.output.resolve(), args.neo4j_container, args.without_robustness)


if __name__ == "__main__":
    raise SystemExit(main())
