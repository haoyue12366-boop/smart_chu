"""Windows 本机故障矩阵；真实停止已有 Neo4j，恢复原状态，所有失败留档。"""

import argparse
import hashlib
import json
import os
import platform
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree

from benchmarks.dataset import ROOT
from benchmarks.runner import source_hashes, write_json

FILES = (
    "tests/fault_injection/test_system_faults.py",
    "tests/fault_injection/test_solver_worker_failure.py",
    "tests/fault_injection/test_commit_boundary.py",
    "tests/fault_injection/test_planning_publish_recovery.py",
    "tests/fault_injection/test_runtime_recovery.py",
    "tests/fault_injection/test_simulation_control_recovery.py",
    "tests/integration/test_plan_compare_and_swap.py",
    "tests/integration/test_notification_stream.py",
    "tests/integration/test_competition_response_recovery.py",
    "tests/contract/test_language_events.py",
)
EVIDENCE_SOURCES = (*FILES, "tests/fault_injection/stream_support.py")
MATRIX = {
    "solver_exit_hang_and_recovery": FILES[:2],
    "real_sqlite_lock": FILES[:1],
    "before_commit_rollback_and_after_commit_response_loss": FILES[2:4],
    "snapshot_corrupt_or_mixed": FILES[:1],
    "neo4j_actually_stopped_cold_full_application": FILES[:1],
    "invalid_llm_no_live_provider_call": (FILES[0], FILES[-1]),
    "stale_revision_and_stale_publication": (FILES[0], FILES[6]),
    "sse_disconnect_cursor_restart": (FILES[0], FILES[7]),
    "execution_failure_stock_not_revived_device_not_released": FILES[4:6],
    "http_retry_business_fact_once": (FILES[0], FILES[8]),
}


def docker_state(name):
    completed = subprocess.run(
        ["docker", "inspect", "--format", "{{json .State}}", name],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return json.loads(completed.stdout)


def port_closed():
    with socket.socket() as probe:
        probe.settimeout(1)
        return probe.connect_ex(("127.0.0.1", 7687)) != 0


def run(output, container):
    output.mkdir(parents=True, exist_ok=False)
    for file in EVIDENCE_SOURCES:
        if not (ROOT / file).is_file():
            raise FileNotFoundError("故障矩阵缺少必需实现：" + file)
    before = docker_state(container)
    began = time.perf_counter_ns()
    report = {
        "status": "RUNNING",
        "formal_acceptance": False,
        "scope": "WINDOWS_REAL_APPLICATION_SQLITE_IPC_SSE_AND_OFFLINE_NEO4J",
        "matrix": MATRIX,
        "source_hashes": source_hashes(),
        "test_hashes": {
            f: hashlib.sha256((ROOT / f).read_bytes()).hexdigest() for f in EVIDENCE_SOURCES
        },
        "started_at": datetime.now(UTC).isoformat(),
        "environment": {"platform": platform.platform(), "python": platform.python_version()},
        "neo4j": {"container": container, "before": before},
        "llm": "Explicit fixed-response injection; no live DeepSeek invocation",
        "timing_scope": (
            "fault request and later recovery separately; not normal performance samples"
        ),
    }
    write_json(output / "report.json", report)
    completed = None
    try:
        if before["Running"]:
            subprocess.run(["docker", "stop", container], cwd=ROOT, check=True, capture_output=True)
        during = docker_state(container)
        report["neo4j"].update(during=during, bolt_port_closed=port_closed())
        if during["Running"] or not report["neo4j"]["bolt_port_closed"]:
            raise RuntimeError("Neo4j 必须实际停止，不以网络替身充当离线验收")
        environment = os.environ.copy()
        environment.update(
            PYTHONUTF8="1",
            SMART_COOKING_P6_OFFLINE_TEST="1",
            SMART_COOKING_TEST_RELEASE_ROOT=str(ROOT / ".tools/p2-regression/releases"),
        )
        command = [
            sys.executable,
            "-m",
            "pytest",
            *FILES,
            "-q",
            "--basetemp",
            str(output / "pytest-temp"),
            "-o",
            "cache_dir=.tmp/pytest-cache",
            "--junitxml",
            str(output / "tests.xml"),
        ]
        with (output / "pytest.log").open("w", encoding="utf-8") as log:
            completed = subprocess.run(
                command, cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT
            )
        report["command"], report["exit_code"] = command, completed.returncode
        root = ElementTree.parse(output / "tests.xml").getroot()
        cases = list(root.iter("testcase"))
        report["tests"] = len(cases)
        report["failures"] = sum(
            c.find("failure") is not None or c.find("error") is not None for c in cases
        )
        report["skips"] = sum(c.find("skipped") is not None for c in cases)
        report["measured_faults"] = {
            p.get("name"): json.loads(p.get("value"))
            for p in root.iter("property")
            if p.get("name", "").startswith("p6_fault:")
        }
        report["source_unchanged"] = report["source_hashes"] == source_hashes()
        report["test_sources_unchanged"] = all(
            report["test_hashes"][f] == hashlib.sha256((ROOT / f).read_bytes()).hexdigest()
            for f in EVIDENCE_SOURCES
        )
        report["status"] = (
            "PASSED"
            if (
                completed.returncode == 0
                and cases
                and not report["failures"]
                and not report["skips"]
                and report["source_unchanged"]
                and report["test_sources_unchanged"]
                and report["measured_faults"]
            )
            else "FAILED"
        )
    finally:
        if before["Running"]:
            subprocess.run(
                ["docker", "start", container], cwd=ROOT, check=True, capture_output=True
            )
        report["neo4j"]["after"] = docker_state(container)
        report["elapsed_ms"] = (time.perf_counter_ns() - began) / 1_000_000
        report["finished_at"] = datetime.now(UTC).isoformat()
        write_json(output / "report.json", report)
    print(
        json.dumps(
            {
                "status": report["status"],
                "tests": report.get("tests"),
                "failures": report.get("failures"),
                "skips": report.get("skips"),
            },
            ensure_ascii=False,
        )
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--neo4j-container", required=True)
    args = parser.parse_args()
    report = run(args.output.resolve(), args.neo4j_container)
    return 0 if report["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
