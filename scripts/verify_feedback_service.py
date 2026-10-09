"""当前修复的 P5 服务/UI 回归；真实断图并恢复，不冒充 P6 全量门槛。"""

import argparse
import hashlib
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


def run(output: Path, container: str) -> int:
    output.mkdir(parents=True, exist_ok=False)
    manifest_path = ROOT / "scripts/verification_manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    files = sorted({name for i in range(1, 9) for name in manifest["tasks"][f"P5-0{i}"]["tests"]})
    before = docker_state(container)
    began = time.perf_counter_ns()
    command = [
        sys.executable,
        "-m",
        "pytest",
        *files,
        "-q",
        "-p",
        "no:cacheprovider",
        "--junitxml",
        str(output / "tests.xml"),
        "--basetemp",
        str(output / "pytest-temp"),
    ]
    report = {
        "status": "RUNNING",
        "scope": "CURRENT_FEEDBACK_FIX_P5_SERVICE_AND_UI_REGRESSION",
        "formal_acceptance": False,
        "full_p6_acceptance": False,
        "started_at": datetime.now(UTC).isoformat(),
        "source_hashes": source_hashes(),
        "test_files": files,
        "command": command,
        "test_hashes": {f: hashlib.sha256((ROOT / f).read_bytes()).hexdigest() for f in files},
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "producer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "neo4j": {"container": container, "before": before},
    }
    write_json(output / "report.json", report)
    try:
        if before["Running"]:
            subprocess.run(["docker", "stop", container], check=True, capture_output=True)
        report["neo4j"].update(during=docker_state(container), bolt_port_closed=port_closed())
        if report["neo4j"]["during"]["Running"] or not report["neo4j"]["bolt_port_closed"]:
            raise ValueError("必须实际断图")
        environment = {
            **os.environ,
            "PYTHONUTF8": "1",
            "SMART_COOKING_TEST_RELEASE_ROOT": str(ROOT / ".tools/p2-regression/releases"),
        }
        with (output / "pytest.log").open("w", encoding="utf-8") as log:
            completed = subprocess.run(
                command, cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT
            )
        cases = list(ElementTree.parse(output / "tests.xml").getroot().iter("testcase"))
        report.update(
            exit_code=completed.returncode,
            tests=len(cases),
            failures=sum(
                c.find("failure") is not None or c.find("error") is not None for c in cases
            ),
            skips=sum(c.find("skipped") is not None for c in cases),
            xml_sha256=hashlib.sha256((output / "tests.xml").read_bytes()).hexdigest(),
            source_unchanged=report["source_hashes"] == source_hashes(),
        )
        report["status"] = (
            "PASSED"
            if (
                completed.returncode == 0
                and cases
                and not report["failures"]
                and not report["skips"]
                and report["source_unchanged"]
            )
            else "FAILED"
        )
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        report.update(status="FAILED", error=str(exc))
    finally:
        if before["Running"]:
            subprocess.run(["docker", "start", container], check=True, capture_output=True)
        report["neo4j"]["after"] = docker_state(container)
        if report["neo4j"]["after"]["Running"] != before["Running"]:
            report.update(status="FAILED", restore_error="Neo4j 未恢复原状态")
        report.update(
            finished_at=datetime.now(UTC).isoformat(),
            elapsed_ms=(time.perf_counter_ns() - began) / 1_000_000,
        )
        write_json(output / "report.json", report)
    print(json.dumps({k: report.get(k) for k in ("status", "tests", "failures", "skips")}))
    return 0 if report["status"] == "PASSED" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--neo4j-container", required=True)
    args = parser.parse_args()
    return run(args.output.resolve(), args.neo4j_container)


if __name__ == "__main__":
    raise SystemExit(main())
