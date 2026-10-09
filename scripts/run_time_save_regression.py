"""在最终源码上统一执行 timeSave 相关回归，绑定实际命令与源码哈希。"""

import hashlib
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

from app.config import ROOT
from benchmarks.runner import source_hashes, write_json

EVIDENCE = ROOT / "benchmarks/reports/verification/P6-timesave-fix-run1"
INPUTS = (
    "regression-run1.xml",
    "regression-retry.xml",
    "engine-final-run3.xml",
    "edge-green.xml",
    "feedback-related.xml",
)
EXTERNAL = (
    "tests/contract/test_fixture_provenance.py::test_reviewed_samples_ready_for_p1_core",
    "tests/contract/test_official_dynamic_profile.py::test_official_dynamic_confirmation_has_hash_bound_source_and_current_profile",
)


def test_hashes() -> dict[str, str]:
    return {
        path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((ROOT / "tests").rglob("*.py"))
    }


def main() -> int:
    modules = {
        case.attrib["classname"]
        for name in INPUTS
        for case in ET.parse(EVIDENCE / name).getroot().iter("testcase")
    }
    files = sorted(name.replace(".", "/") + ".py" for name in modules)
    assert len(files) == 41 and all((ROOT / name).is_file() for name in files)
    report = EVIDENCE / "regression-final.xml"
    log = EVIDENCE / "regression-final.log"
    manifest = EVIDENCE / "regression-final-run.json"
    assert not any(p.exists() for p in (report, log, manifest)), "保留已有运行证据"
    command = [
        sys.executable,
        "-m",
        "pytest",
        *files,
        "-q",
        "-p",
        "no:cacheprovider",
        "--junitxml",
        str(report),
        *("--deselect=" + item for item in EXTERNAL),
    ]
    environment = {
        **os.environ,
        "PYTHONUTF8": "1",
        "SMART_COOKING_TEST_RELEASE_ROOT": str(ROOT / ".tools/p2-regression/releases"),
    }
    original_source, original_tests = source_hashes(), test_hashes()
    started_at, started = datetime.now(UTC).isoformat(), time.monotonic()
    with log.open("w", encoding="utf-8") as stream:
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
        )
        assert process.stdout is not None
        for line in process.stdout:
            stream.write(line)
            stream.flush()
            print(line, end="", flush=True)
        exit_code = process.wait()
    write_json(
        manifest,
        {
            "started_at": started_at,
            "finished_at": datetime.now(UTC).isoformat(),
            "elapsed_sec": time.monotonic() - started,
            "command": command,
            "exit_code": exit_code,
            "report": report.relative_to(ROOT).as_posix(),
            "report_sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
            "source_hashes": original_source,
            "test_hashes": original_tests,
            "source_unchanged": source_hashes() == original_source,
            "tests_unchanged": test_hashes() == original_tests,
            "external_gates_deselected": list(EXTERNAL),
            "test_release_root": environment["SMART_COOKING_TEST_RELEASE_ROOT"],
            "producer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        },
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
