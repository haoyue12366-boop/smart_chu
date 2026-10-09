"""固定 P4 发布包的内部服务采样；模拟事件，不声明协议或实机验证。"""

import argparse
import hashlib
import json
import math
import os
import platform
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from app.domain.events import RuntimeEvent
from app.domain.policy import SchedulingPolicy
from app.knowledge.loader import read_release_ref
from app.knowledge.repository import SnapshotKnowledgeRepository
from app.runtime.clock import SimulationClock
from app.runtime.service import RuntimeService
from app.scheduling.worker import SolverWorker
from app.services.planning import PlanningService
from app.storage.repositories import RuntimeRepository
from app.storage.unit_of_work import UnitOfWork
from app.validation.schedule import ScheduleValidator

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = datetime.fromisoformat("2026-09-29T10:00:00+08:00")
RELEASE_ID = "delegated-v3-p4-preparation-v1-all"


def summary(rows):
    samples = sorted(row["service_elapsed_ms"] for row in rows)
    if not samples:
        raise ValueError("性能报告不得用空样本生成百分位")

    def percentile(fraction):
        return samples[max(0, math.ceil(len(samples) * fraction) - 1)]

    return {
        "sample_count": len(rows),
        "p50_ms": percentile(0.5),
        "p95_ms": percentile(0.95),
        "max_ms": max(samples),
        "failure_count": sum(row["status"] != "PUBLISHED" for row in rows),
        "deadline_exceeded_count": sum(
            row["service_elapsed_ms"] > row["budget_ms"] for row in rows
        ),
        "solver_fallback_count": sum(row["solver_fallback"] for row in rows),
        "failure_reasons": dict(
            Counter(row["failure_reason"] for row in rows if row["failure_reason"])
        ),
        "fallback_reasons": dict(
            Counter(reason for row in rows for reason in row["fallback_reasons"])
        ),
    }


def make_event(runtime, session_id, identity, kind, payload):
    session = runtime.get(session_id)
    state = session.runtime
    return RuntimeEvent(
        event_id=identity,
        session_id=state.session_id,
        event_type=kind,
        occurred_at=runtime.clock.now(),
        received_at=runtime.clock.now(),
        source="SIMULATED",
        expected_state_revision=state.state_revision,
        base_plan_version=state.current_plan_version,
        payload=payload,
    )


def measure(planning, request, *, case, mode, stage, worker):
    ready_before = worker.is_alive and worker._ready
    started = time.perf_counter_ns()
    result = planning.apply_event(request)
    elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
    detail = result.planning
    fallback = bool(detail and detail.solver_fallback_used)
    return {
        "case_id": case,
        "mode": mode,
        "stage": stage,
        "status": result.status,
        "service_elapsed_ms": elapsed_ms,
        "budget_ms": result.budget_ms,
        "failure_reason": detail.failure.message if detail and detail.failure else None,
        "solver_fallback": fallback,
        "fallback_reasons": [
            f"{phase.objective_stage}:{phase.status}:"
            f"{phase.diagnostic_message or '未接受完整CP候选'}"
            for phase in detail.stage_results
            if phase.candidate is None
        ]
        if fallback and detail
        else [],
        "phase_timings": [timing.model_dump(mode="json") for timing in detail.timings]
        if detail
        else [],
        "solver_stages": [
            phase.model_dump(mode="json", exclude={"candidate"}) for phase in detail.stage_results
        ]
        if detail
        else [],
        "state_revision": result.event.state_revision if result.event else None,
        "plan_version": result.plan.plan_version if result.plan else None,
        "candidate_source": detail.selected_candidate_source if detail else None,
        "worker_pid": worker.process_id,
        "worker_ready_before": ready_before,
        "worker_restarts": worker.restart_count,
    }


def verify_persisted(runtime, session_id):
    session = runtime.get(session_id)
    with runtime.store.engine.connect() as tx:
        repository = RuntimeRepository(tx)
        plan = repository.plan(session_id, session.runtime.current_plan_version)
        problem = repository.problem(session_id, session.runtime.current_plan_version)
    if plan is None:
        raise ValueError("成功采样缺少已提交计划")
    proof = ScheduleValidator().validate(
        runtime.knowledge, problem.runtime, problem, plan.validated.candidate
    )
    if not proof.valid:
        raise ValueError("采样计划重载后未通过独立校验")


def prepare_worker(worker, records, *, case, iteration, phase):
    for attempt in range(1, 3):
        began = time.perf_counter_ns()
        ready = worker.warmup()
        records.append(
            {
                "case_id": case,
                "iteration": iteration,
                "phase": phase,
                "attempt": attempt,
                "elapsed_ms": (time.perf_counter_ns() - began) / 1_000_000,
                "ready": ready,
            }
        )
        if ready:
            return
        if attempt == 1:
            worker.close()
    raise TimeoutError("基准准备预热失败，两次尝试均已记录")


def run(samples, output):
    if type(samples) is not int or samples < 1:
        raise ValueError("每场景预热样本数必须为正整数")
    output = output.resolve()
    if output.exists():
        raise FileExistsError("保留既有性能证据，请使用新的输出目录：" + str(output))
    output.mkdir(parents=True)
    code_hashes = {
        path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((ROOT / "app").rglob("*.py"))
    }
    code_hashes["benchmarks/runtime_replanning.py"] = hashlib.sha256(
        Path(__file__).read_bytes()
    ).hexdigest()
    definitions_path = ROOT / "benchmarks/scenarios/runtime_events.json"
    definitions = json.loads(definitions_path.read_bytes())
    assert definitions["release_id"] == RELEASE_ID
    policy_path = ROOT / "data/policies/p4-runtime-v1.json"
    policy = SchedulingPolicy.model_validate_json(policy_path.read_bytes())
    loaded_at = time.perf_counter_ns()
    release_root = ROOT / "data/preparations/p4-v1/releases"
    reference = read_release_ref(release_root, RELEASE_ID)
    repository = SnapshotKnowledgeRepository(release_root)
    repository.load(reference)
    with repository.acquire(reference) as lease:
        knowledge = lease.select(
            tuple(recipe.recipe_id for recipe in lease.loaded.snapshot.knowledge.recipes)
        )
    loading_ms = (time.perf_counter_ns() - loaded_at) / 1_000_000
    recipes = {recipe.recipe_id.root: recipe for recipe in knowledge.recipes}
    rows = []
    prewarm = []
    databases = []
    worker = SolverWorker()
    try:
        for case_number, case in enumerate(definitions["performance_cases"]):
            for iteration in range(samples + 1):
                mode = "COLD" if iteration == 0 else "WARM"
                # 每场景的冷样本包含求解进程首次准备；预热时间单独记录，不藏进服务指标。
                if iteration == 0:
                    worker.close()
                else:
                    prepare_worker(
                        worker,
                        prewarm,
                        case=case["case_id"],
                        iteration=iteration,
                        phase="before_initial",
                    )
                sid = f"p4-bench-{case_number}-{iteration}"
                database = output / (sid + ".sqlite")
                store = UnitOfWork(database)
                store.migrate()
                runtime = RuntimeService(store, knowledge, SimulationClock(ORIGIN))
                runtime.create_session(sid, "SIMULATED", ORIGIN, policy)
                planning = PlanningService(runtime, worker)
                initial = make_event(
                    runtime,
                    sid,
                    sid + "-start",
                    "START_SESSION",
                    {
                        "recipes": [
                            {"id": recipes[identity].recipe_id, "name": recipes[identity].name}
                            for identity in case["initial_recipe_ids"]
                        ],
                    },
                )
                initial_row = measure(
                    planning,
                    initial,
                    case=case["case_id"],
                    mode=mode,
                    stage="INITIAL",
                    worker=worker,
                )
                current = runtime.get(sid)
                if case["event_type"] == "ADD_RECIPE":
                    recipe = recipes[case["recipe_id"]]
                    payload = {"recipes": [{"id": recipe.recipe_id, "name": recipe.name}]}
                else:
                    payload = {"recipe_instance_id": current.menu[0].recipe_instance_id}
                    if case["event_type"] == "DELAY_RECIPE":
                        payload["earliest_start_sec"] = case["earliest_start_sec"]
                request = make_event(runtime, sid, sid + "-change", case["event_type"], payload)
                # 真正预热重排单独确认进程状态；冷样本保持服务中准备进程的实际行为。
                if mode == "WARM":
                    prepare_worker(
                        worker,
                        prewarm,
                        case=case["case_id"],
                        iteration=iteration,
                        phase="before_replan",
                    )
                changed_row = measure(
                    planning,
                    request,
                    case=case["case_id"],
                    mode=mode,
                    stage="REPLAN",
                    worker=worker,
                )
                rows.extend((initial_row, changed_row))
                if changed_row["status"] == "PUBLISHED":
                    verify_persisted(runtime, sid)
                store.close()
                databases.append(
                    database.relative_to(ROOT).as_posix()
                    if database.is_relative_to(ROOT)
                    else str(database)
                )
                with (output / "samples.jsonl").open("a", encoding="utf-8") as handle:
                    for row in (initial_row, changed_row):
                        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                print(
                    case["case_id"],
                    mode,
                    iteration,
                    changed_row["status"],
                    round(changed_row["service_elapsed_ms"]),
                    flush=True,
                )
    finally:
        worker.close()
    report = {
        "kind": "P4_RUNTIME_SERVICE_BENCHMARK",
        "status": "COMPLETED",
        "recorded_at": datetime.now(UTC).isoformat(),
        "command": [sys.executable, *sys.argv],
        "scope": "真实内部服务、算法、独立校验和SQLite；显式模拟事件；"
        "不包含HTTP、前端、官方协议或实机。",
        "cold_definition": "每场景第一组请求从冷进程开始；初排后的重排可能已有预热，"
        "每个请求记录 worker_ready_before，初排与重排分别统计。",
        "release": reference.model_dump(mode="json"),
        "policy": policy.model_dump(mode="json"),
        "knowledge_loading_ms": loading_ms,
        "hardware": {
            "platform": platform.platform(),
            "processor": platform.processor(),
            "cpu_count": os.cpu_count(),
        },
        "clock_info": {
            "deadline": vars(time.get_clock_info("monotonic")),
            "measurement": vars(time.get_clock_info("perf_counter")),
        },
        "prewarm_samples": prewarm,
        "prewarm_failure_count": sum(not attempt["ready"] for attempt in prewarm),
        "prewarm_definition": "服务测量之外至多两次原定 10 秒预热，逐次记录失败、耗时与重启；"
        "只有已确认就绪的请求列为 WARM。请求本身的初排及重排预算保持不变。",
        "samples_per_case_warm": samples,
        "source_artifacts": {
            path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (policy_path, definitions_path)
        },
        "code_hashes": code_hashes,
        "results": {
            mode: {
                stage: summary(
                    [row for row in rows if row["mode"] == mode and row["stage"] == stage]
                )
                for stage in ("INITIAL", "REPLAN")
            }
            for mode in ("COLD", "WARM")
        },
        "case_results": {
            case["case_id"]: summary(
                [
                    row
                    for row in rows
                    if row["stage"] == "REPLAN"
                    and row["mode"] == "WARM"
                    and row["case_id"] == case["case_id"]
                ]
            )
            for case in definitions["performance_cases"]
        },
        "database_artifacts": databases,
        "samples_artifact": "samples.jsonl",
    }
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    report = run(arguments.samples, arguments.output)
    print(json.dumps(report["results"], ensure_ascii=False))


if __name__ == "__main__":
    main()
