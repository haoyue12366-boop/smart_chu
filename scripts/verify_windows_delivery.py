"""监督真实 Windows 离线安装测试，停止知识维护服务并恢复原状态。"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree

from benchmarks.dataset import ROOT
from benchmarks.fault_injection import docker_state, port_closed
from benchmarks.runner import source_hashes, write_json
from scripts.package_release import sha256, verify_delivery

VERIFICATION_FILES = (
    "scripts/verify_windows_delivery.py",
    "scripts/package_release.py",
    "tests/integration/test_deployment_smoke.py",
    "benchmarks/fault_injection.py",
    "docs/P6-Windows部署说明.md",
    "deploy/windows/Install.ps1",
    "deploy/windows/Start.ps1",
    "deploy/windows/Stop.ps1",
    "deploy/windows/Verify.ps1",
)


def run(output, archive, container):
    output.mkdir(parents=True, exist_ok=False)
    delivery = archive.with_suffix("")
    began = time.perf_counter_ns()
    before = docker_state(container)
    report = {
        "status": "RUNNING",
        "scope": "WINDOWS_DEVELOPMENT_OFFLINE_INSTALL_RESTART",
        "formal_acceptance": False,
        "started_at": datetime.now(UTC).isoformat(),
        "source_hashes": source_hashes(),
        "verification_hashes": {name: sha256(ROOT / name) for name in VERIFICATION_FILES},
        "delivery": {
            "archive": str(archive.relative_to(ROOT)),
            "archive_sha256": sha256(archive),
            "directory": str(delivery.relative_to(ROOT)),
            **verify_delivery(delivery),
        },
        "neo4j": {"container": container, "before": before},
        "live_llm_calls": 0,
    }
    write_json(output / "report.json", report)
    try:
        if before["Running"]:
            subprocess.run(["docker", "stop", container], check=True, capture_output=True)
        report["neo4j"].update(during=docker_state(container), bolt_port_closed=port_closed())
        if report["neo4j"]["during"]["Running"] or not report["neo4j"]["bolt_port_closed"]:
            raise ValueError("Neo4j 必须实际停止且 Bolt 端口关闭")
        command = [
            sys.executable,
            "-m",
            "pytest",
            "tests/integration/test_deployment_smoke.py",
            "-q",
            "-o",
            "cache_dir=.tmp/pytest-cache",
            "--basetemp",
            str(output / "pytest-temp"),
            "--junitxml",
            str(output / "tests.xml"),
        ]
        with (output / "pytest.log").open("w", encoding="utf-8") as log:
            result = subprocess.run(
                command,
                cwd=ROOT,
                env={**os.environ, "PYTHONUTF8": "1", "SMART_COOKING_P6_DELIVERY": str(archive)},
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=300,
            )
        report["command"], report["exit_code"] = command, result.returncode
        xml = output / "tests.xml"
        cases = list(ElementTree.parse(xml).getroot().iter("testcase"))
        report["tests"] = len(cases)
        report["failures"] = sum(
            c.find("failure") is not None or c.find("error") is not None for c in cases
        )
        report["skips"] = sum(c.find("skipped") is not None for c in cases)
        report["xml_hash"] = sha256(xml)
        evidence = list((output / "pytest-temp").rglob("windows-deployment-evidence.json"))
        report["installation_evidence"] = [
            {"path": str(path.relative_to(ROOT)), "sha256": sha256(path)} for path in evidence
        ]
        report["source_unchanged"] = report["source_hashes"] == source_hashes()
        report["verification_unchanged"] = all(
            digest == sha256(ROOT / name) for name, digest in report["verification_hashes"].items()
        )
        report["status"] = (
            "PASSED"
            if result.returncode == 0
            and len(cases) == 1
            and not report["failures"]
            and not report["skips"]
            and len(evidence) == 1
            and report["source_unchanged"]
            and report["verification_unchanged"]
            else "FAILED"
        )
    except (OSError, ValueError, subprocess.SubprocessError, ElementTree.ParseError) as exc:
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
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--neo4j-container", required=True)
    args = parser.parse_args()
    report = run(args.output.resolve(), args.archive.resolve(), args.neo4j_container)
    print(
        json.dumps(
            {
                "status": report["status"],
                "tests": report.get("tests"),
                "error": report.get("error"),
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
