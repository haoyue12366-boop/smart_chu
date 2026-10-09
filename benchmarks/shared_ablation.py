"""同知识、菜单、设备和预算四开关对照；文件产物不冒充P4事务运行库。"""

import argparse
import json
import math
import platform
import time
from pathlib import Path

from app.compiler.compiler import ProblemCompiler
from app.domain.base import content_hash
from app.domain.knowledge import MenuKnowledgeView
from app.domain.policy import SchedulingPolicy
from app.domain.ports import Deadline, PlanningRequest
from app.domain.reports import PlanningResult
from app.domain.runtime_snapshot import RuntimeSnapshot
from app.domain.scheduling_problem import SchedulingProblem
from app.scheduling.model_builder import ModelBuilder
from app.scheduling.solution_mapping import map_solution
from app.scheduling.worker import SolverWorker
from app.services.planning_core import PlanningCore
from app.validation.schedule import ScheduleValidator
from benchmarks.shared_cases import ROOT, case_inputs, load_cases, load_published_knowledge


def read_trial(path):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    knowledge = MenuKnowledgeView.model_validate(payload["knowledge"])
    runtime = RuntimeSnapshot.model_validate(payload["runtime"])
    result = PlanningResult.model_validate(payload["result"])
    if content_hash(knowledge) != payload["knowledge_input_hash"]:
        raise ValueError("实验知识输入哈希不一致")
    if result.status == "VALIDATED":
        problem = SchedulingProblem.model_validate(payload["problem"])
        if problem.runtime != runtime:
            raise ValueError("实验问题与运行快照不一致")
        proof = ScheduleValidator().validate(knowledge, runtime, problem, result.candidate)
        if not proof.valid:
            raise ValueError("持久化计划重载后未通过独立校验：" + proof.model_dump_json())
    return payload


def run_trial(base, case, shared, thermal, worker, path):
    knowledge, menu, runtime = case_inputs(base, case)
    policy = SchedulingPolicy(
        policy_version="p3-controlled-ablation-v1",
        shared_prep=shared,
        strict_together_batch=thermal,
        allow_delegated_shared_estimates=True,
    )
    request = PlanningRequest(request_id="p3-ablation", menu=menu, policy=policy)
    warm_started = time.monotonic_ns()
    warmup_ok = worker.warmup()
    warmup_ms = (time.monotonic_ns() - warm_started) // 1_000_000
    restarts_before = worker.restart_count
    core = PlanningCore(solver=worker)
    previous_builds = len(worker.build_reports)
    started = time.monotonic_ns()
    result = core.compute(
        request,
        knowledge,
        runtime,
        Deadline(expires_at_ns=started + policy.initial_budget.total_ms * 1_000_000),
    )
    service_ms = (time.monotonic_ns() - started) // 1_000_000
    problem = core.last_problem
    builds = worker.build_reports[previous_builds:]
    payload = {
        "kind": "P3_STATIC_EXPERIMENT_ARTIFACT",
        "case": case,
        "knowledge_input_hash": content_hash(knowledge),
        "knowledge": knowledge.model_dump(mode="json"),
        "runtime": runtime.model_dump(mode="json"),
        "request": request.model_dump(mode="json"),
        "result": result.model_dump(mode="json"),
        "problem": problem.model_dump(mode="json") if problem else None,
        "builds": [b.model_dump(mode="json") for b in builds],
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    artifact_started = time.monotonic_ns()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    read_trial(path)
    artifact_ms = (time.monotonic_ns() - artifact_started) // 1_000_000
    chosen = {a.carrier_id for a in result.candidate.assignments} if result.candidate else set()
    return {
        "case_id": case["case_id"],
        "source_kind": case["source_kind"],
        "shared_prep": shared,
        "strict_together_batch": thermal,
        "knowledge_input_hash": content_hash(knowledge),
        "runtime_hash": content_hash(runtime),
        "policy": policy.model_dump(mode="json"),
        "status": result.status,
        "service_ms": service_ms,
        "worker_ready_before_request": warmup_ok,
        "worker_warmup_ms": warmup_ms,
        "worker_restarts_during_request": worker.restart_count - restarts_before,
        "artifact_roundtrip_ms": artifact_ms,
        "artifact_path": str(path),
        "reloaded_validation_valid": True if result.status == "VALIDATED" else None,
        "first_validated_candidate_ms": result.first_validated_candidate_ms,
        "selected_candidate_source": result.selected_candidate_source,
        "solver_fallback_used": result.solver_fallback_used,
        "failure": result.failure.model_dump(mode="json") if result.failure else None,
        "metrics": result.candidate.metrics.model_dump(mode="json")
        if result.candidate and result.candidate.metrics
        else None,
        "phase_timings": [t.model_dump(mode="json") for t in result.timings],
        "candidate_counts": {
            "standalone": len(problem.standalone_candidates),
            "shared": len(problem.shared_prep_candidates),
            "thermal": len(problem.thermal_batch_candidates),
        }
        if problem
        else None,
        "selected_shared": sum(c.carrier_id in chosen for c in problem.shared_prep_candidates)
        if problem
        else 0,
        "selected_thermal": sum(c.carrier_id in chosen for c in problem.thermal_batch_candidates)
        if problem
        else 0,
        "candidate_generation_report": problem.candidate_generation_report.model_dump(mode="json")
        if problem
        else None,
        "model_size_estimate": problem.model_size_estimate.model_dump(mode="json")
        if problem
        else None,
        "estimated_proto_bytes": problem.model_estimated_proto_bytes if problem else None,
        "builds": [b.model_dump(mode="json", exclude={"constraint_mappings"}) for b in builds],
    }


def late_batch_witness(base, case):
    from ortools.sat.python import cp_model

    knowledge, menu, runtime = case_inputs(base, case)
    policy = SchedulingPolicy(
        policy_version="synthetic-forced-late-witness",
        strict_together_batch=True,
        allow_delegated_shared_estimates=True,
    )
    problem = ProblemCompiler().compile(
        knowledge,
        menu,
        runtime,
        policy,
        Deadline(expires_at_ns=time.monotonic_ns() + 10_000_000_000),
    )
    if not isinstance(problem, SchedulingProblem) or len(problem.thermal_batch_candidates) != 1:
        raise ValueError("迟到批次见证缺少唯一共同候选")
    spans = []
    for joint in (False, True):
        builder = ModelBuilder(
            problem, Deadline(expires_at_ns=time.monotonic_ns() + 10_000_000_000)
        )
        builder.build()
        builder.model.add(
            builder.selected[problem.thermal_batch_candidates[0].carrier_id] == int(joint)
        )
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 3
        solver.parameters.num_search_workers = 1
        solver.parameters.random_seed = 42
        status = solver.solve(builder.model)
        if status != cp_model.OPTIMAL:
            raise ValueError("受约束的迟到见证未证明最优，不能报告确定比较")
        candidate = map_solution(builder, solver)
        if not ScheduleValidator().validate(knowledge, runtime, problem, candidate).valid:
            raise ValueError("迟到见证独立校验失败")
        spans.append(solver.value(builder.makespan))
    return {
        "source_kind": "SYNTHETIC_DEPENDENCY",
        "comparison": "FORCED_JOINT_VS_STANDALONE_NOT_MAIN_POLICY",
        "knowledge_input_hash": content_hash(knowledge),
        "standalone_valid": True,
        "joint_valid": True,
        "standalone_makespan_sec": spans[0],
        "forced_joint_makespan_sec": spans[1],
        "source_operation_durations_changed": False,
    }


def summarize(rows):
    values = sorted(r["service_ms"] for r in rows)

    def quantile(q):
        return values[max(0, math.ceil(len(values) * q) - 1)]

    failures = [r for r in rows if r["status"] == "FAILED"]
    return {
        "sample_count": len(rows),
        "success_count": len(rows) - len(failures),
        "failure_count": len(failures),
        "service_p50_ms": quantile(0.5),
        "service_p95_ms": quantile(0.95),
        "service_max_ms": max(values),
        "budget_failure_rate": sum(
            r["failure"] is not None
            and r["failure"]["failure_class"] == "NO_SOLUTION_WITHIN_BUDGET"
            for r in rows
        )
        / len(rows),
        "fallback_rate": sum(r["solver_fallback_used"] for r in rows) / len(rows),
        "failure_classes": {
            kind: sum(r["failure"]["failure_class"] == kind for r in failures)
            for kind in {r["failure"]["failure_class"] for r in failures}
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "benchmarks/reports/P3-shared-ablation-v1"
    )
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("repeats必须为正整数")
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("实验输出目录非空；使用新的目录以保留既有结果")
    start = time.monotonic_ns()
    base = load_published_knowledge()
    cold_load_ms = (time.monotonic_ns() - start) // 1_000_000
    cases = load_cases()
    rows = []
    with SolverWorker() as worker:
        startup_ms = worker.startup_ms
        for repeat in range(args.repeats):
            for case in cases:
                for shared, thermal in ((False, False), (True, False), (False, True), (True, True)):
                    name = f"{case['case_id']}-{int(shared)}{int(thermal)}-{repeat}.json"
                    row = run_trial(base, case, shared, thermal, worker, args.output / name)
                    row["repeat"] = repeat
                    rows.append(row)
                    print(
                        json.dumps(
                            {
                                "case": case["case_id"],
                                "switches": [shared, thermal],
                                "status": row["status"],
                                "service_ms": row["service_ms"],
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
    witness = late_batch_witness(
        base, next(c for c in cases if c["case_id"] == "synthetic-late-member")
    )
    report = {
        "suite_id": "p3-shared-ablation-v1",
        "release": base.release.model_dump(mode="json"),
        "formal_human_review_complete": False,
        "scope": "STATIC_CORE_AND_FILE_ARTIFACTS; SQLite runtime transactions belong to P4",
        "seed": 42,
        "repeats": args.repeats,
        "platform": platform.platform(),
        "processor": platform.processor(),
        "cold_knowledge_load_ms": cold_load_ms,
        "cold_worker_startup_ms": startup_ms,
        "summary": summarize(rows),
        "by_case": {
            case["case_id"]: summarize([r for r in rows if r["case_id"] == case["case_id"]])
            for case in cases
        },
        "forced_late_batch_witness": witness,
        "rows": rows,
        "limitations": [
            "Small fixed suite, not a performance guarantee for all menus",
            "Device timing remains delegated development estimates",
            "Four-switch comparisons share identical inputs except the two feature flags",
            "Requests warm the worker first; recovery and artifact I/O are reported separately",
        ],
    }
    (args.output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report["summary"], ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
