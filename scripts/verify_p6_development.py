"""监督 P6 开发验收：真实断图、阶段命令、独立归档及原服务状态恢复。"""

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
from benchmarks.fault_injection import docker_state, port_closed
from benchmarks.runner import source_hashes, write_json
from scripts.package_release import sha256

VERIFICATION_FILES = (
    "scripts/verify_p6_development.py",
    "scripts/verify.py",
    "scripts/verification_manifest.json",
    "data/development/authorizations/p6-final-acceptance-v1.json",
    "data/preparations/p6-v1/acceptance_inputs.json",
)


def run(output, container):
    output.mkdir(parents=True, exist_ok=False)
    began = time.perf_counter_ns()
    before = docker_state(container)
    canonical = ROOT / "benchmarks/reports/verification/P6-development.json"
    if canonical.exists():
        shutil.copyfile(canonical, output / "previous-phase-report.json")
    command = [sys.executable, "-m", "scripts.verify", "--phase", "P6", "--gate", "development"]
    report = {
        "status": "RUNNING",
        "scope": "WINDOWS_DEVELOPMENT_TECHNICAL_ACCEPTANCE",
        "formal_acceptance": False,
        "started_at": datetime.now(UTC).isoformat(),
        "command": command,
        "source_hashes": source_hashes(),
        "verification_hashes": {name: sha256(ROOT / name) for name in VERIFICATION_FILES},
        "neo4j": {"container": container, "before": before},
        "live_llm_calls": 0,
    }
    write_json(output / "report.json", report)
    try:
        if before["Running"]:
            subprocess.run(["docker", "stop", container], check=True, capture_output=True)
        report["neo4j"].update(during=docker_state(container), bolt_port_closed=port_closed())
        if report["neo4j"]["during"]["Running"] or not report["neo4j"]["bolt_port_closed"]:
            raise ValueError("阶段验收必须实际断开 Neo4j，不以环境标记替代")
        with (output / "gate.log").open("w", encoding="utf-8") as log:
            completed = subprocess.run(
                command,
                cwd=ROOT,
                env={
                    **os.environ,
                    "PYTHONUTF8": "1",
                    "SMART_COOKING_TEST_RELEASE_ROOT": str(ROOT / ".tools/p2-regression/releases"),
                },
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        report["exit_code"] = completed.returncode
        frozen = output / "phase-report.json"
        shutil.copyfile(canonical, frozen)
        phase = json.loads(frozen.read_bytes())
        report["phase_report"] = {
            "path": frozen.relative_to(ROOT).as_posix(),
            "sha256": sha256(frozen),
            "status": phase["status"],
            "code_fingerprint": phase["code_fingerprint"],
        }
        report["source_unchanged"] = report["source_hashes"] == source_hashes()
        report["verification_unchanged"] = all(
            digest == sha256(ROOT / name) for name, digest in report["verification_hashes"].items()
        )
        passed = (
            completed.returncode == 0
            and phase["status"] == "DEVELOPMENT_VERIFIED"
            and all(
                phase["tasks"][f"P6-0{i}"]["status"] == "DEVELOPMENT_VERIFIED" for i in range(1, 8)
            )
            and report["source_unchanged"]
            and report["verification_unchanged"]
        )
        report["status"] = "PASSED" if passed else "FAILED"
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        report.update(status="FAILED", error=str(exc))
    finally:
        try:
            if before["Running"]:
                subprocess.run(["docker", "start", container], check=True, capture_output=True)
            report["neo4j"]["after"] = docker_state(container)
            if report["neo4j"]["after"]["Running"] != before["Running"]:
                report.update(status="FAILED", restore_error="Neo4j 未恢复原状态")
        except (OSError, subprocess.SubprocessError) as exc:
            report.update(status="FAILED", restore_error=str(exc))
        report["elapsed_ms"] = (time.perf_counter_ns() - began) / 1_000_000
        report["finished_at"] = datetime.now(UTC).isoformat()
        write_json(output / "report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--neo4j-container", required=True)
    args = parser.parse_args()
    report = run(args.output.resolve(), args.neo4j_container)
    print(
        json.dumps({"status": report["status"], "error": report.get("error")}, ensure_ascii=False)
    )
    return 0 if report["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
