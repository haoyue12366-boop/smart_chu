"""逐个复核七组扰动归档、终态证明、原始初排和当前源码身份。"""

import argparse
import gzip
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from benchmarks.dataset import ROOT
from benchmarks.robustness.feedback_runner import GROUPS
from benchmarks.runner import source_hashes, write_json


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(directory: Path, output: Path) -> dict[str, object]:
    if output.exists():
        raise FileExistsError("不能覆盖旧审计")
    report_path = directory / "report.json"
    report = json.loads(report_path.read_bytes())
    if report["status"] != "EXPERIMENT_COMPLETED":
        raise ValueError("实验没有实际完成")
    if not report["source_unchanged"] or report["source_hashes"] != source_hashes():
        raise ValueError("实验不覆盖当前源码")
    if not report["original_report_unchanged"]:
        raise ValueError("原始报告被改写")
    original = ROOT / report["baseline_report"]["path"]
    if digest(original) != report["baseline_report"]["sha256"]:
        raise ValueError("原始报告身份不一致")
    counts = Counter(row["group"] for row in report["rows"])
    expected = report["expected_trajectories"]
    if (
        set(counts) != set(GROUPS)
        or len(set(counts.values())) != 1
        or len(report["rows"]) != expected
        or report["actual_trajectories"] != expected
        or sum(counts.values()) != expected
    ):
        raise ValueError("缺失配对轨迹或分母")
    keys = {(r["case_id"], r["group"], r["trajectory"]) for r in report["rows"]}
    if len(keys) != expected:
        raise ValueError("存在重复轨迹")
    files = report["artifact_hashes"]
    if set(files) != {p.name for p in directory.glob("*.json.gz")}:
        raise ValueError("归档清单不完整")
    for name, sha in files.items():
        path = (directory / name).resolve()
        if not path.is_relative_to(directory.resolve()) or digest(path) != sha:
            raise ValueError("归档损坏或越界：" + name)
    completed = Counter()
    sources = Counter()
    for row in report["rows"]:
        frozen = json.loads(gzip.decompress((directory / row["artifact"]).read_bytes()))
        for field in ("status", "group", "case_id", "trajectory", "initial_candidate_hash"):
            if frozen[field] != row[field]:
                raise ValueError("轨迹摘要与归档不一致：" + row["artifact"])
        if row["status"] == "COMPLETED":
            if (
                not frozen["validation"]["valid"]
                or frozen["completed_task_count"] != frozen["required_task_count"]
            ):
                raise ValueError("完成轨迹缺少独立终态证明")
            completed[row["group"]] += 1
        elif not frozen["failure"] or not frozen["failure_detail"]["code"]:
            raise ValueError("失败轨迹缺少明确原因")
        if not row["group"].endswith("CONTINUOUS") and row["replan_count"] > 1:
            raise ValueError("原单次触发对照被改变")
    for group, summary in report["summaries"].items():
        if (
            summary["completed"] != completed[group]
            or summary["failed"] + completed[group] != counts[group]
            or summary["spread_denominator"] != completed[group]
            or summary["failure_rate"] != summary["failed"] / counts[group]
        ):
            raise ValueError("失败或完成差分母不一致：" + group)
    for name, sha in report["original_initial_artifacts"].items():
        if digest(original.parent / name) != sha or digest(directory / name) != sha:
            raise ValueError("原四组初排字节被改变")
    for row in report["replan_computations"]:
        name = f"replan-{row['observed_key']}.json.gz"
        if name not in files or row["cache_hit"]:
            raise ValueError("实际计算清单缺少归档或重复计入缓存")
        frozen = json.loads(gzip.decompress((directory / name).read_bytes()))
        if frozen["request_scope"] != "OBSERVED_FACTS_ONLY_NO_FUTURE_SEED":
            raise ValueError("重排请求来源不明")
        if row["failure"] is None:
            if row["compute_elapsed_ms"] >= 2400 or not frozen["result"]["validation"]["valid"]:
                raise ValueError("成功重排违反截止预算或独立校验")
        sources[str(row.get("selected_candidate_source"))] += 1
    result = {
        "status": "PASSED",
        "created_at": datetime.now(UTC).isoformat(),
        "report_path": report_path.relative_to(ROOT).as_posix(),
        "report_sha256": digest(report_path),
        "producer_sha256": digest(Path(__file__)),
        "partial": report["partial"],
        "formal_acceptance": False,
        "files": files,
        "source_hashes": report["source_hashes"],
        "source_exclusions": [],
        "group_counts": dict(counts),
        "completed_terminal_scans": sum(completed.values()),
        "actual_replan_computations": len(report["replan_computations"]),
        "candidate_sources": dict(sources),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.directory.resolve(), args.output.resolve())
    print(json.dumps({k: v for k, v in result.items() if k not in {"files", "source_hashes"}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
