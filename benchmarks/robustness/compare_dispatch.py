"""固定已归档初排，配对重放修复前/后模拟器；隔离重新初排的求解差异。"""

import argparse
import gzip
import hashlib
import importlib.util
import json
import os
import platform
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from app.domain.base import content_hash
from app.domain.reports import PlanningResult
from app.domain.runtime_session import RuntimeSession
from app.domain.scheduling_problem import SchedulingProblem
from app.scheduling.worker import SolverWorker
from app.validation.schedule import ScheduleValidator
from benchmarks.dataset import ROOT, load_inputs
from benchmarks.robustness.runner import CONFIG, ObservedPlanner, freeze, summary
from benchmarks.robustness.simulator import FutureDurations, TrajectorySimulator
from benchmarks.runner import source_hashes, write_json


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_legacy(baseline: Path) -> tuple[type, Path, dict[str, object], dict[str, object]]:
    manifest = json.loads(baseline.read_bytes())
    entry = manifest["benchmarks/robustness/simulator.py"]
    source = (ROOT / entry["path"]).resolve()
    original = (ROOT / manifest["original_report"]["path"]).resolve()
    if not source.is_relative_to(ROOT) or not original.is_relative_to(ROOT):
        raise ValueError("对照证据必须位于项目目录")
    if (
        sha256(source) != entry["sha256"]
        or sha256(original) != manifest["original_report"]["sha256"]
    ):
        raise ValueError("修复前源码或原报告指纹变化")
    report = json.loads(original.read_bytes())
    if report["source_hashes"]["benchmarks/robustness/simulator.py"] != entry["sha256"]:
        raise ValueError("修复前源码不是原报告记录的版本")
    spec = importlib.util.spec_from_file_location("robustness_legacy_dispatch", source)
    if spec is None or spec.loader is None:
        raise ValueError("无法加载修复前模拟器")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.TrajectorySimulator, original, report, manifest


def run(output: Path, baseline: Path, cases: list[str], trajectories: int) -> dict[str, object]:
    legacy, original, previous, baseline_manifest = load_legacy(baseline)
    config = json.loads(CONFIG.read_bytes())
    if config != previous["config"] or not 1 <= trajectories <= config["trajectories"]:
        raise ValueError("配对实验须保持原扰动分布且不扩大轨迹范围")
    if not cases or len(cases) != len(set(cases)):
        raise ValueError("菜单列表为空或重复")
    output.mkdir(parents=True, exist_ok=False)
    base, _ = load_inputs()
    if base.release.model_dump(mode="json") != previous["release"]:
        raise ValueError("知识发布与原实验不同")
    before = source_hashes()
    began = time.perf_counter_ns()
    report = {
        "status": "RUNNING",
        "scope": "PAIRED_FIXED_INITIAL_PLAN_SIMULATOR_COMPARISON",
        "formal_acceptance": False,
        "partial": True,
        "started_at": datetime.now(UTC).isoformat(),
        "config": config,
        "case_ids": cases,
        "trajectories_per_case_group_version": trajectories,
        "release": previous["release"],
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "processor": platform.processor(),
            "logical_cpus": os.cpu_count(),
        },
        "suite_hash": previous["suite_hash"],
        "source_hashes": before,
        "baseline_manifest": baseline_manifest,
        "initial_artifacts": {},
        "rows": [],
        "replan_computations": [],
        "simulators": ["legacy-shift-v1", "observed-start-window-v2"],
        "controls": [
            "两版本共用归档的已验证初排；不重新求解初排",
            "相同菜单、种子、时长分布、固定知识、2400ms重排预算与单次触发规则",
            "两版本共用完整观察状态缓存；相同状态取得同一重排结果",
            "旧模拟器按原报告源码指纹加载；新模拟器仅修正窗口和失败诊断",
            "执行仍为离线纯事件路径；不代替持续反馈、真实HTTP和全量验收",
        ],
    }
    write_json(output / "report.json", report)
    with SolverWorker() as worker:
        for case_id in cases:
            for duration_policy in ("NOMINAL", "BUFFERED"):
                path = original.parent / f"initial-{case_id}-{duration_policy}.json.gz"
                report["initial_artifacts"][path.name] = sha256(path)
                frozen = json.loads(gzip.decompress(path.read_bytes()))
                if frozen["session"] is None:
                    raise ValueError("小规模执行对照需要已有完整初排：" + path.name)
                session = RuntimeSession.model_validate(frozen["session"])
                problem = SchedulingProblem.model_validate(frozen["problem"])
                result = PlanningResult.model_validate(frozen["result"])
                if result.candidate is None:
                    raise ValueError("归档缺少完整初排候选")
                ids = {i.recipe_id for i in session.menu}
                knowledge = base.model_copy(
                    update={
                        "recipes": tuple(r for r in base.recipes if r.recipe_id in ids),
                        "recipe_contexts": tuple(
                            c for c in base.recipe_contexts if c.recipe_id in ids
                        ),
                    }
                )
                proof = ScheduleValidator().validate(
                    knowledge, problem.runtime, problem, result.candidate
                )
                if not proof.valid:
                    raise ValueError("归档初排未通过当前独立校验：" + path.name)
                planner = ObservedPlanner(worker, result.candidate, problem, output)
                for trajectory in range(trajectories):
                    for method in ("SHIFT", "REPLAN"):
                        group = duration_policy + "_" + method
                        versions = [
                            ("legacy-shift-v1", legacy),
                            ("observed-start-window-v2", TrajectorySimulator),
                        ]
                        if trajectory % 2:
                            versions.reverse()
                        for version, implementation in versions:
                            future = FutureDurations(config["seed"], case_id, trajectory, config)
                            simulation = implementation(
                                knowledge,
                                session,
                                problem,
                                result.candidate,
                                future,
                                config,
                                planner=planner if method == "REPLAN" else None,
                            )
                            row = simulation.run()
                            row.update(
                                case_id=case_id,
                                trajectory=trajectory,
                                group=group,
                                simulator_version=version,
                                initial_problem_hash=problem.problem_hash,
                                initial_candidate_hash=content_hash(result.candidate),
                            )
                            artifact = (
                                f"trajectory-{case_id}-{group}-{trajectory:03d}-{version}.json.gz"
                            )
                            freeze(output / artifact, row)
                            report["rows"].append(
                                {
                                    k: v
                                    for k, v in row.items()
                                    if k
                                    not in {
                                        "events",
                                        "final_session",
                                        "validation",
                                        "dispatch_diagnostics",
                                    }
                                }
                                | {"artifact": artifact}
                            )
                report["replan_computations"].extend(planner.computations)
                print(
                    json.dumps(
                        {
                            "case_id": case_id,
                            "policy": duration_policy,
                            "completed_rows": len(report["rows"]),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                write_json(output / "report.json", report)
    report["summaries"] = {
        version: {
            group: summary(
                [
                    r
                    for r in report["rows"]
                    if r["group"] == group and r["simulator_version"] == version
                ]
            )
            for group in config["groups"]
        }
        for version in report["simulators"]
    }
    paired = {}
    for group in config["groups"]:
        pairs = {}
        for row in report["rows"]:
            if row["group"] == group:
                pairs.setdefault((row["case_id"], row["trajectory"]), {})[
                    row["simulator_version"]
                ] = row["status"]
        paired[group] = dict(
            Counter(
                p["legacy-shift-v1"] + "->" + p["observed-start-window-v2"] for p in pairs.values()
            )
        )
    report["paired_outcomes"] = paired
    report["artifact_hashes"] = {p.name: sha256(p) for p in sorted(output.glob("*.json.gz"))}
    report["expected_trajectories"] = len(cases) * trajectories * 4 * 2
    report["actual_trajectories"] = len(report["rows"])
    report["source_unchanged"] = before == source_hashes()
    report["original_report_unchanged"] = (
        sha256(original) == baseline_manifest["original_report"]["sha256"]
    )
    report["initial_artifacts_unchanged"] = all(
        sha256(original.parent / name) == digest
        for name, digest in report["initial_artifacts"].items()
    )
    report["elapsed_ms"] = (time.perf_counter_ns() - began) / 1_000_000
    report["finished_at"] = datetime.now(UTC).isoformat()
    report["status"] = (
        "EXPERIMENT_COMPLETED"
        if (
            report["source_unchanged"]
            and report["original_report_unchanged"]
            and report["initial_artifacts_unchanged"]
            and report["actual_trajectories"] == report["expected_trajectories"]
        )
        else "INVALID_EXPERIMENT"
    )
    write_json(output / "report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--case-id", action="append", required=True)
    parser.add_argument("--trajectories", type=int, default=10)
    args = parser.parse_args()
    report = run(args.output, args.baseline, args.case_id, args.trajectories)
    print(
        json.dumps(
            {"status": report["status"], "paired_outcomes": report["paired_outcomes"]},
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0 if report["status"] == "EXPERIMENT_COMPLETED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
