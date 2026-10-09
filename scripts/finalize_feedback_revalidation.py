"""汇总用户取消全量后的局部修复证据，拒绝冒充完整 P6 验收。"""

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree

from benchmarks.dataset import ROOT
from benchmarks.runner import source_hashes, write_json
from scripts.run_feedback_revalidation import RELATED

EVIDENCE = ROOT / "benchmarks/reports/verification/P6-feedback-critical-fix"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_report(name: str, status: str, current: dict[str, str]) -> dict[str, object]:
    report = json.loads((ROOT / name).read_bytes())
    if report["status"] != status or report.get("source_hashes") != current:
        raise ValueError("报告状态或当前源码不匹配：" + name)
    return report


def check_xml(path: Path, expected: int | None = None) -> int:
    cases = list(ElementTree.parse(path).getroot().iter("testcase"))
    if not cases or any(
        case.find(tag) is not None for case in cases for tag in ("failure", "error", "skipped")
    ):
        raise ValueError("测试缺失或存在失败/跳过：" + str(path))
    if expected is not None and len(cases) != expected:
        raise ValueError("测试数量不符：" + str(path))
    return len(cases)


def finalize(output: Path) -> dict[str, object]:
    if output.exists():
        raise FileExistsError("局部交付清单不得覆盖旧版本")
    current = source_hashes()
    paths = {
        "experiment": "benchmarks/reports/P6-feedback-critical-small-run2/report.json",
        "audit": "benchmarks/reports/verification/P6-feedback-critical-fix/small2-audit.json",
        "supervisor": "benchmarks/reports/verification/P6-feedback-revalidation-run1/report.json",
        "properties": "data/verification/P6-property-summary-feedback-run1.json",
        "faults": "benchmarks/reports/P6-faults-feedback-run1/report.json",
        "performance": "benchmarks/reports/P6-performance-feedback-run1/report.json",
        "windows": "data/verification/P6-windows-feedback-run1/report.json",
        "service": "data/verification/P6-feedback-service-run1/report.json",
    }
    statuses = {
        "experiment": "EXPERIMENT_COMPLETED",
        "audit": "PASSED",
        "supervisor": "LIMITED_REVALIDATION_COMPLETED_FULL_OMITTED_BY_USER",
        "properties": "PASSED",
        "faults": "PASSED",
        "performance": "TARGETS_MET",
        "windows": "PASSED",
        "service": "PASSED",
    }
    reports = {key: read_report(name, statuses[key], current) for key, name in paths.items()}
    experiment, audit, supervisor = (reports[key] for key in ("experiment", "audit", "supervisor"))
    if (
        experiment["partial"] is not True
        or experiment["formal_acceptance"] is not False
        or experiment["actual_trajectories"] != 210
        or audit["source_exclusions"] != []
        or audit["report_sha256"] != digest(ROOT / paths["experiment"])
        or audit["producer_sha256"] != digest(ROOT / "scripts/audit_feedback_experiment.py")
        or supervisor["full_robustness_included"] is not False
        or supervisor["user_scope_quote"] != "不用全量"
    ):
        raise ValueError("局部实验身份、来源或用户范围不符")
    stages = supervisor["stages"]
    forbidden = {"full-340", "ablation-240", "robustness-28000", "robustness-audit"}
    required = {
        "small-audit",
        "ruff",
        "format",
        "mypy",
        "related-tests",
        "frontend-types",
        "frontend-tests",
        "frontend-build",
        "properties",
        "property-summary",
        "faults",
        "performance-153",
        "windows-package",
        "windows-smoke",
    }
    if {row["name"] for row in stages} != required or any(
        row["name"] in forbidden or row["status"] != "PASSED" for row in stages
    ):
        raise ValueError("监督入口包含未授权全量或未通过阶段")
    performance = reports["performance"]
    if performance["expected_requests"] != 153 or len(performance["samples"]) != 153:
        raise ValueError("性能请求数量不足")
    if reports["faults"]["tests"] != 53 or reports["windows"]["tests"] != 1:
        raise ValueError("故障或 Windows 复验数量不符")
    for key in ("faults", "windows", "service"):
        report = reports[key]
        if not report["source_unchanged"] or report["failures"] or report["skips"]:
            raise ValueError("复验存在失败或源码变化：" + key)
        graph = report["neo4j"]
        if (
            graph["during"]["Running"]
            or not graph["bolt_port_closed"]
            or graph["before"]["Running"] != graph["after"]["Running"]
        ):
            raise ValueError("真实断图或恢复证据不完整：" + key)
    xml_paths = {
        "related": ROOT / Path(paths["supervisor"]).parent / "related-tests.xml",
        "partial_evidence": EVIDENCE / "partial-evidence-green.xml",
        "frontend": EVIDENCE / "frontend-unit-green.xml",
        "properties": ROOT / "data/verification/P6-properties-feedback-run1.xml",
        "faults": ROOT / "benchmarks/reports/P6-faults-feedback-run1/tests.xml",
        "windows": ROOT / "data/verification/P6-windows-feedback-run1/tests.xml",
        "service": ROOT / "data/verification/P6-feedback-service-run1/tests.xml",
    }
    expected = {
        "related": 66,
        "partial_evidence": 2,
        "properties": 17,
        "frontend": 17,
        "faults": 53,
        "windows": 1,
        "service": 74,
    }
    tests = {key: check_xml(path, expected.get(key)) for key, path in xml_paths.items()}
    archive = ROOT / "data/delivery/P6-Windows-feedback-run1.zip"
    windows = reports["windows"]
    if windows["delivery"]["archive_sha256"] != digest(archive):
        raise ValueError("Windows 实测 ZIP 身份不一致")
    package_path = ROOT / "data/delivery/P6-Windows-feedback-run1/release_manifest.json"
    package = json.loads(package_path.read_bytes())
    app_hashes = {name: value for name, value in current.items() if name.startswith("app/")}
    packaged_app = {
        name: value for name, value in package["files"].items() if name.startswith("app/")
    }
    if packaged_app != app_hashes:
        raise ValueError("交付包未包含完整当前生产源码")
    for entry in windows["installation_evidence"]:
        if digest(ROOT / entry["path"]) != entry["sha256"]:
            raise ValueError("独立安装证据身份改变")
    evidence_paths = {
        **{key: ROOT / name for key, name in paths.items()},
        **{key + "_xml": path for key, path in xml_paths.items()},
        "package_manifest": package_path,
        "windows_archive": archive,
        "comparison": EVIDENCE / "comparison-summary.json",
        "production_audit": EVIDENCE / "production-source-audit.json",
        "fast_policy": ROOT / "data/policies/p6-feedback-fast-v1.json",
        "critical_policy": ROOT / "data/policies/p6-feedback-critical-v1.json",
    }
    result = {
        "schema_version": "1.0",
        "status": "LOCAL_IMPLEMENTATION_VERIFIED_PARTIAL_ROBUSTNESS_HAS_FAILURES",
        "created_at": datetime.now(UTC).isoformat(),
        "formal_acceptance": False,
        "full_p6_acceptance": False,
        "user_scope_quote": "不用全量",
        "omitted": sorted(forbidden),
        "source_hashes": current,
        "frontend_source_hashes": {
            path.relative_to(ROOT).as_posix(): digest(path)
            for path in sorted((ROOT / "web/src").rglob("*"))
            if path.is_file()
        },
        "source_exclusions": [],
        "producer_sha256": digest(Path(__file__)),
        "test_hashes": {
            name: digest(ROOT / name)
            for name in (
                *RELATED,
                "tests/integration/test_feedback_experiment_evidence.py",
                "tests/integration/test_p6_robustness_evidence.py",
            )
        },
        "tests": tests,
        "performance_requests": 153,
        "experiment_trajectories": 210,
        "experiment_group_outcomes": {
            group: {key: row[key] for key in ("trajectory_count", "completed", "failed")}
            for group, row in experiment["summaries"].items()
        },
        "artifacts": {
            key: {"path": path.relative_to(ROOT).as_posix(), "sha256": digest(path)}
            for key, path in evidence_paths.items()
        },
        "limitations": [
            "210 synthetic paired trajectories do not establish full robustness",
            "Continuous feedback improves this sample completion count but worsens finish spread",
            "SERIAL_RECIPE_V1 shows no extra benefit here; it remains disabled by default",
            "Fast parallel plans can lack the verified reference required for official timeSave",
            "Formal release, official dynamic integration and public deployment remain open",
        ],
    }
    write_json(output, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = finalize(args.output.resolve())
    print(json.dumps({"status": result["status"], "tests": result["tests"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
