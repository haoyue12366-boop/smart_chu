"""当前窗口修复的统一回归；旧 timeSave/正式门槛证据保持不变。"""

import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime

from app.config import ROOT
from benchmarks.runner import source_hashes, write_json
from scripts.run_time_save_regression import EXTERNAL, INPUTS, test_hashes

EVIDENCE = ROOT / "benchmarks/reports/verification/P6-dynamic-window-fix-run1"


def main() -> int:
    previous = ROOT / "benchmarks/reports/verification/P6-timesave-fix-run1"
    modules = {
        case.attrib["classname"]
        for name in INPUTS
        for case in ET.parse(previous / name).getroot().iter("testcase")
    }
    files = sorted(
        {name.replace(".", "/") + ".py" for name in modules}
        | {
            "tests/integration/test_tight_dispatch_guard.py",
            "tests/integration/test_dispatch_guard_runtime.py",
            "tests/property/test_system_properties.py",
            "tests/stateful/test_runtime_invariants.py",
        }
    )
    target = EVIDENCE / "regression-final.xml"
    assert not target.exists()
    command = [
        sys.executable,
        "-m",
        "pytest",
        *files,
        "-q",
        "-p",
        "no:cacheprovider",
        "--junitxml",
        str(target),
        *("--deselect=" + name for name in EXTERNAL),
    ]
    environment = {
        **os.environ,
        "PYTHONUTF8": "1",
        "SMART_COOKING_TEST_RELEASE_ROOT": str(ROOT / ".tools/p2-regression/releases"),
    }
    source, tests = source_hashes(), test_hashes()
    began = time.monotonic()
    with (EVIDENCE / "regression-final.log").open("w", encoding="utf-8") as log:
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
            log.write(line)
            log.flush()
            print(line, end="", flush=True)
        code = process.wait()
    cases = list(ET.parse(target).getroot().iter("testcase"))
    write_json(
        EVIDENCE / "regression-final-run.json",
        {
            "finished_at": datetime.now(UTC).isoformat(),
            "command": command,
            "exit_code": code,
            "elapsed_sec": time.monotonic() - began,
            "source_hashes": source,
            "test_hashes": tests,
            "source_unchanged": source == source_hashes(),
            "tests_unchanged": tests == test_hashes(),
            "test_count": len(cases),
            "failures": sum(
                c.find("failure") is not None or c.find("error") is not None for c in cases
            ),
            "skips": sum(c.find("skipped") is not None for c in cases),
            "external_gates_deselected": list(EXTERNAL),
            "test_release_root": environment["SMART_COOKING_TEST_RELEASE_ROOT"],
        },
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
