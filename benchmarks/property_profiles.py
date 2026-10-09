"""属性验收保存实际 XML 数量与工具版本；配置数不替代已执行数量。"""

import argparse
import hashlib
import json
import platform
from importlib.metadata import version
from pathlib import Path
from xml.etree import ElementTree

from benchmarks.dataset import ROOT
from benchmarks.runner import source_hashes, write_json


def collect(xml_files):
    counts = {}
    tests = []
    for path in xml_files:
        root = ElementTree.parse(path).getroot()
        for item in root.iter("property"):
            name = item.get("name", "")
            if name.startswith(("p6_property:", "p6_full_session:")):
                if name in counts:
                    raise ValueError("同一性质的执行次数不能跨报告重复计数")
                counts[name] = int(item.get("value", "0"))
        for case in root.iter("testcase"):
            tests.append(
                {
                    "name": case.get("name"),
                    "class": case.get("classname"),
                    "evidence": str(path),
                    "failed": case.find("failure") is not None or case.find("error") is not None,
                    "skipped": case.find("skipped") is not None,
                }
            )
    properties = {k: v for k, v in counts.items() if k.startswith("p6_property:")}
    sufficient = len(properties) >= 10 and all(v >= 200 for v in properties.values())
    sufficient = sufficient and counts.get("p6_full_session:sequences", 0) >= 100
    passed_ids = {(t["class"], t["name"]) for t in tests if not t["failed"] and not t["skipped"]}
    unresolved = [t for t in tests if t["skipped"] and (t["class"], t["name"]) not in passed_ids]
    resolved = [t for t in tests if t["skipped"] and (t["class"], t["name"]) in passed_ids]
    test_sources = [
        *ROOT.glob("tests/property/*.py"),
        *ROOT.glob("tests/stateful/*.py"),
        ROOT / "tests/runtime_support.py",
        ROOT / "tests/unit/test_schedule_validator.py",
        ROOT / "tests/unit/test_joint_thermal_batches.py",
    ]
    return {
        "status": "PASSED"
        if sufficient and tests and not unresolved and not any(t["failed"] for t in tests)
        else "FAILED",
        "scope": "FINITE_SYNTHETIC_SYSTEM_PROPERTIES_AND_REAL_SQLITE_SEQUENCES",
        "hypothesis": version("hypothesis"),
        "pytest": version("pytest"),
        "python": platform.python_version(),
        "derandomize": True,
        "derandomize_scope": (
            "Ten P6 system properties and full_session; older tests retain their explicit profiles"
        ),
        "database": None,
        "property_max_examples": 200,
        "sequence_max_examples": 100,
        "sequence_max_steps": 50,
        "counts": counts,
        "tests": tests,
        "observed_skipped_count": sum(t["skipped"] for t in tests),
        "unresolved_skips": unresolved,
        "resolved_by_actual_same_test_rerun": resolved,
        "source_hashes": source_hashes(),
        "test_hashes": {
            p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in test_sources
        },
        "evidence": [str(p) for p in xml_files],
        "evidence_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in xml_files},
        "limitations": [
            "Finite examples are not a proof over arbitrary schedules",
            "Two independent synthetic sessions test different material/device paths",
            "Future observed-duration distributions are evaluated separately",
            "Hypothesis shrinks failures; replay traces are saved by failing sequence assertions",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xml", action="append", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("属性证据不能覆盖旧报告")
    report = collect(args.xml)
    write_json(args.output, report)
    print(json.dumps({"status": report["status"], "counts": report["counts"]}, ensure_ascii=False))
    return 0 if report["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
