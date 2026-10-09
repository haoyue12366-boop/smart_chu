"""单因素受控消融；所有尝试与失败留档，图谱只用于离线对照。"""

import argparse
import gzip
import json
import math
import os
import platform
import time
from datetime import UTC, datetime
from pathlib import Path

from app.compiler.compiler import ProblemCompiler
from app.config import AppSettings
from app.domain.base import content_hash
from app.domain.knowledge import MenuKnowledgeView
from app.domain.objectives import ObjectiveStage
from app.domain.policy import SchedulingPolicy
from app.domain.ports import Deadline
from app.domain.scheduling_problem import SchedulingProblem
from app.knowledge.index import build_index, validate_index
from app.knowledge.loader import load_release, read_release_ref
from app.knowledge.snapshot import SchedulingKnowledgeSnapshot
from app.pipeline.snapshot_export import SnapshotExporter
from app.scheduling.candidate_pool import CandidatePool
from app.scheduling.greedy import GreedyScheduler
from app.scheduling.serial_reference import serial_order_holds
from app.scheduling.worker import SolverWorker
from app.validation.schedule import ScheduleValidator
from benchmarks.dataset import ROOT, load_inputs, suite_hash, validate_suite
from benchmarks.runner import source_hashes, write_json
from benchmarks.shared_cases import case_inputs

CONFIG = ROOT / "benchmarks/scenarios/ablation_policies.json"
FACTORS = {
    "algorithm": {"algorithm"},
    "shared_prep": {"shared_prep"},
    "strict_together_batch": {"strict_together_batch"},
    "graph_bound_preprocessing": {"graph_bound_preprocessing"},
    "equivalence_deduplication": {"equivalence_deduplication"},
    "candidate_caps": {"max_nonstandalone_per_requirement", "max_nonstandalone_per_problem"},
}


def group_policy(base, group):
    allowed = set().union(*FACTORS.values()) - {"algorithm"}
    extra = set(group) - allowed - {"id", "algorithm"}
    if extra:
        raise ValueError("实验组修改了未声明因素：" + str(sorted(extra)))
    values = base.model_dump(mode="json")
    values.update({k: v for k, v in group.items() if k in allowed})
    return SchedulingPolicy.model_validate(values)


def controlled_values(base, group):
    values = group_policy(base, group).model_dump(mode="json")
    values["graph_bound_preprocessing"] = group.get("graph_bound_preprocessing", True)
    values["equivalence_deduplication"] = group.get("equivalence_deduplication", True)
    values["algorithm"] = group["algorithm"]
    return values


def validate_comparisons(config, base):
    groups = {g["id"]: g for g in config["groups"]}
    if len(groups) != len(config["groups"]) or config["repeats"] < 3:
        raise ValueError("实验组身份重复或重复次数不足")
    for comparison in config["comparisons"]:
        left = controlled_values(base, groups[comparison["control"]])
        right = controlled_values(base, groups[comparison["treatment"]])
        changed = {k for k in left.keys() | right.keys() if left.get(k) != right.get(k)}
        if not changed or not changed <= FACTORS[comparison["factor"]]:
            raise ValueError("对照混入其他因素或实际没有变化：" + str(changed))
    return True


def graph_json_comparison(output, cases, base, policy):
    """实际读取已有固定图版本；不覆盖、不重新生成历史发布。"""
    from neo4j import GraphDatabase

    from app.knowledge.graph_projection import GraphProjector

    settings = AppSettings()
    reference = read_release_ref(settings.release_root, settings.release_id)
    loaded = load_release(settings.release_root, reference)
    source = loaded.snapshot.knowledge
    local = {}
    env_path = ROOT / ".env"
    if env_path.exists():
        local = dict(
            line.split("=", 1)
            for line in env_path.read_text(encoding="utf-8").splitlines()
            if line and not line.startswith("#") and "=" in line
        )
    local.update(os.environ)
    started = time.perf_counter_ns()
    with GraphDatabase.driver(
        local["NEO4J_URI"], auth=(local["NEO4J_USERNAME"], local["NEO4J_PASSWORD"])
    ) as driver:
        driver.verify_connectivity()
        build = SnapshotExporter(GraphProjector(driver)).export_snapshot(source)
    graph_ms = (time.perf_counter_ns() - started) / 1_000_000
    started = time.perf_counter_ns()
    restored_source = type(source).model_validate_json(source.model_dump_json())
    snapshot = SchedulingKnowledgeSnapshot(
        snapshot_id="snapshot-" + restored_source.content_hash,
        content_hash=restored_source.content_hash,
        build_time=build.snapshot.build_time,
        knowledge=restored_source,
    )
    index = build_index(restored_source)
    validate_index(restored_source, index)
    json_ms = (time.perf_counter_ns() - started) / 1_000_000
    if snapshot != build.snapshot or index != build.index or snapshot != loaded.snapshot:
        # 发布时间不属于排程语义；已发布快照必须逐实体及内容哈希相同。
        if (
            snapshot.knowledge != loaded.snapshot.knowledge
            or snapshot.content_hash != loaded.snapshot.content_hash
            or snapshot.snapshot_id != loaded.snapshot.snapshot_id
            or snapshot != build.snapshot
            or index != build.index
        ):
            raise ValueError("规范 JSON、真实图谱和当前发布不等价")

    def view(route_snapshot, route_index):
        route_source = route_snapshot.knowledge
        return MenuKnowledgeView(
            release=base.release,
            snapshot_schema_version=route_snapshot.snapshot_schema_version,
            snapshot_hash=route_snapshot.content_hash,
            recipes=route_source.recipes,
            devices=route_source.scope.devices,
            profiles=route_source.profiles,
            rules=route_source.rules,
            provenance_index=route_index.evidence,
            device_choices=route_source.scope.device_choices,
            recipe_contexts=route_source.scope.recipe_contexts,
        )

    graph_view = view(build.snapshot, build.index)
    json_view = view(snapshot, index)
    if graph_view != json_view:
        raise ValueError("不同来源菜单视图不等价")
    rows = []
    for case in cases:
        inputs = []
        for origin, route_view in (("GRAPH", graph_view), ("JSON", json_view)):
            knowledge, menu, runtime = case_inputs(route_view, case)
            started = time.perf_counter_ns()
            problem = ProblemCompiler().compile(
                knowledge,
                menu,
                runtime,
                policy,
                Deadline(
                    expires_at_ns=time.monotonic_ns() + policy.initial_budget.total_ms * 1_000_000
                ),
            )
            if not isinstance(problem, SchedulingProblem):
                raise ValueError("等价通路编译失败：" + problem.model_dump_json())
            inputs.append(problem)
            rows.append(
                {
                    "case_id": case["case_id"],
                    "origin": origin,
                    "problem_hash": problem.problem_hash,
                    "compile_ms": (time.perf_counter_ns() - started) / 1_000_000,
                }
            )
        if inputs[0] != inputs[1]:
            raise ValueError("相同语义知识产生不同问题")
    result = {
        "status": "PASSED",
        "scope": "REAL_OFFLINE_GRAPH_EXPORT_VS_CANONICAL_JSON",
        "release": base.release.model_dump(mode="json"),
        "graph_export_with_connect_ms": graph_ms,
        "json_build_ms": json_ms,
        "semantic_equal": True,
        "database_changes_mathematical_optimum_claimed": False,
        "rows": rows,
    }
    write_json(output / "graph-json.json", result)
    return result


def run_trial(base, base_policy, case, group, repeat, worker, output):
    knowledge, menu, runtime = case_inputs(base, case)
    policy = group_policy(base_policy, group)
    started = time.monotonic_ns()
    deadline = Deadline(expires_at_ns=started + policy.initial_budget.total_ms * 1_000_000)
    compute_end = deadline.expires_at_ns - policy.initial_budget.publication_reserve_ms * 1_000_000
    compile_started = time.monotonic_ns()
    problem = ProblemCompiler().compile(knowledge, menu, runtime, policy, deadline)
    row = {
        "case_id": case["case_id"],
        "group": group["id"],
        "repeat": repeat,
        "source_kind": case["source_kind"],
        "knowledge_hash": content_hash(knowledge),
        "runtime_hash": content_hash(runtime),
        "policy": policy.model_dump(mode="json"),
        "compile_ms": (time.monotonic_ns() - compile_started) / 1_000_000,
        "status": "FAILED",
        "failure": None,
        "first_validated_candidate_ms": None,
    }
    artifact = {
        "case": case,
        "group": group,
        "knowledge": knowledge.model_dump(mode="json"),
        "runtime": runtime.model_dump(mode="json"),
        "problem": problem.model_dump(mode="json"),
    }
    if not isinstance(problem, SchedulingProblem):
        row["failure"] = problem.model_dump(mode="json")
    else:
        pool = CandidatePool(problem, knowledge, runtime, ScheduleValidator(), None)
        algorithm = group["algorithm"]
        hint = None
        greedy = None
        if algorithm in {"GREEDY", "CP_HINT"}:
            greedy = GreedyScheduler().solve(
                problem,
                Deadline(
                    expires_at_ns=min(
                        compute_end,
                        time.monotonic_ns() + policy.initial_budget.greedy_ms * 1_000_000,
                    )
                ),
            )
            if greedy.candidate is not None and pool.add(greedy.candidate):
                hint = greedy.candidate
                row["first_validated_candidate_ms"] = (time.monotonic_ns() - started) / 1_000_000
        result = None
        before_builds = len(worker.build_reports)
        if algorithm != "GREEDY" and time.monotonic_ns() < compute_end:
            result = worker.solve(
                problem,
                hint,
                Deadline(
                    expires_at_ns=min(
                        compute_end,
                        time.monotonic_ns() + policy.initial_budget.solver_ms * 1_000_000,
                    )
                ),
                serial_menu=algorithm == "SERIAL_CP",
                stage=None if algorithm == "SERIAL_CP" else ObjectiveStage(name="A_MAKESPAN"),
            )
            if result.candidate is not None and time.monotonic_ns() < deadline.expires_at_ns:
                if pool.add(result.candidate) and row["first_validated_candidate_ms"] is None:
                    row["first_validated_candidate_ms"] = (
                        time.monotonic_ns() - started
                    ) / 1_000_000
        chosen = pool.best(makespan_first=True)
        if chosen is not None:
            proof = ScheduleValidator().validate(knowledge, runtime, problem, chosen.candidate)
            if not proof.valid or (
                algorithm == "SERIAL_CP" and not serial_order_holds(chosen.candidate, problem)
            ):
                raise AssertionError("消融成功候选未通过独立校验")
            row["status"] = (
                "VALIDATED" if time.monotonic_ns() < deadline.expires_at_ns else "DEADLINE_EXCEEDED"
            )
            row["metrics"] = chosen.candidate.metrics.model_dump(mode="json")
            artifact["candidate"] = chosen.candidate.model_dump(mode="json")
            artifact["validation"] = proof.model_dump(mode="json")
        row.update(
            {
                "problem_hash": problem.problem_hash,
                "solver_status": result.status if result else None,
                "best_bound": result.best_bound if result else None,
                "objective_stage": result.objective_stage if result else None,
                "greedy_status": greedy.status if greedy else None,
                "hint_supplied": hint is not None,
                "candidate_report": problem.candidate_generation_report.model_dump(mode="json"),
                "model_size": problem.model_size_estimate.model_dump(mode="json"),
                "mandatory_program_hash": content_hash(problem.mandatory_programs),
                "builds": [
                    b.model_dump(mode="json", exclude={"constraint_mappings"})
                    for b in worker.build_reports[before_builds:]
                ],
                "rejections": [r.model_dump(mode="json") for r in pool.rejections],
            }
        )
        artifact["solve_result"] = result.model_dump(mode="json") if result else None
        artifact["greedy_result"] = greedy.model_dump(mode="json") if greedy else None
        if chosen is None:
            row["failure"] = (
                result.diagnostic_message
                if result
                else greedy.reason
                if greedy
                else "No complete candidate"
            )
    row["total_ms"] = (time.monotonic_ns() - started) / 1_000_000
    path = output / f"{case['case_id']}-{group['id']}-{repeat}.json.gz"
    artifact["summary"] = row
    with path.open("xb") as stream:
        stream.write(
            gzip.compress(json.dumps(artifact, ensure_ascii=False).encode("utf-8"), mtime=0)
        )
    row["artifact"] = path.name
    return row


def summarize(rows):
    values = sorted(row["total_ms"] for row in rows)
    return {
        "attempts": len(rows),
        "valid": sum(r["status"] == "VALIDATED" for r in rows),
        "failures": sum(r["status"] != "VALIDATED" for r in rows),
        "p50_ms": values[math.ceil(len(values) * 0.5) - 1],
        "p95_ms": values[math.ceil(len(values) * 0.95) - 1],
        "max_ms": values[-1],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    base, policy = load_inputs()
    config = json.loads(args.config.read_bytes())
    validate_comparisons(config, policy)
    suite = json.loads((ROOT / "benchmarks/scenarios/full_suite.json").read_bytes())
    validate_suite(suite, base, policy)
    by_id = {
        c["case_id"]: c
        for key in ("single_recipes", "combinations", "boundaries")
        for c in suite[key]
    }
    cases = [
        dict(by_id[identity], source_kind="REAL_PUBLISHED_RECIPES_SYNTHETIC_MENU")
        for identity in config["case_ids"]
    ]
    args.output.mkdir(parents=True, exist_ok=False)
    before = source_hashes()
    report = {
        "status": "RUNNING",
        "scope": "CONTROLLED_STATIC_DEVELOPMENT",
        "formal_acceptance": False,
        "started_at": datetime.now(UTC).isoformat(),
        "config": config,
        "config_hash": suite_hash(config),
        "suite_hash": suite["suite_hash"],
        "source_hashes": before,
        "platform": platform.platform(),
        "rows": [],
    }
    try:
        report["graph_json"] = graph_json_comparison(args.output, cases, base, policy)
        with SolverWorker() as worker:
            for repeat in range(config["repeats"]):
                for case in cases:
                    # 轮换组顺序以减少固定顺序/升温偏差；预算和样本均不变。
                    offset = repeat % len(config["groups"])
                    groups = config["groups"][offset:] + config["groups"][:offset]
                    for group in groups:
                        if not worker.warmup():
                            raise TimeoutError("下一组前工作进程恢复失败")
                        row = run_trial(base, policy, case, group, repeat, worker, args.output)
                        report["rows"].append(row)
                        print(
                            f"{len(report['rows'])}: {case['case_id']} "
                            f"{group['id']} {row['status']}",
                            flush=True,
                        )
                        write_json(
                            args.output / "progress.json",
                            {k: v for k, v in report.items() if k != "source_hashes"},
                        )
        report["status"] = "EXPERIMENT_COMPLETED"
    finally:
        report["source_unchanged"] = before == source_hashes()
        report["completed_at"] = datetime.now(UTC).isoformat()
        report["by_group"] = {
            g["id"]: summarize([r for r in report["rows"] if r["group"] == g["id"]])
            for g in config["groups"]
            if any(r["group"] == g["id"] for r in report["rows"])
        }
        report["limitations"] = [
            "All failed and slower trials retained",
            "No non-equivalent pruning without certified replacement rules",
            "OPTIMAL applies only to recorded retained model and objective stage",
            "No claim that graph storage changes mathematical optimum",
            "This comparison does not certify HTTP or persistent publication latency",
        ]
        write_json(args.output / "report.json", report)
    return 0 if report["status"] == "EXPERIMENT_COMPLETED" and report["source_unchanged"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
