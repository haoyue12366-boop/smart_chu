"""Windows 真实 HTTP 三轮单并发采样；失败、长尾和独立扫描完整留档。"""

import argparse
import gzip
import hashlib
import json
import math
import os
import platform
import re
import subprocess
import time
from collections import Counter
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from fastapi.routing import APIRoute
from sqlalchemy import URL, create_engine
from sqlalchemy.pool import NullPool
from starlette.concurrency import run_in_threadpool

from app.api.competition_adapter import CompetitionAdapter
from app.domain.candidates import stable_id
from app.domain.competition_decimal import DecimalCompetitionResponse
from app.storage.competition_tasks import HttpRequestRepository
from app.storage.repositories import RuntimeRepository
from app.validation.competition_contract import validate_competition_projection
from app.validation.schedule import ScheduleValidator
from benchmarks.dataset import ROOT, load_inputs, suite_hash, validate_suite
from benchmarks.runner import source_hashes, write_json
from tests.fault_injection.stream_support import WindowsApiProcess


def create_benchmark_app():
    """真实应用附加实验监督端口；此模块不进入 Windows 生产交付包。"""
    from app.main import create_app

    app = create_app()

    async def prepare_worker():
        worker = app.state.container.worker
        attempts = []
        for _ in range(2):
            began = time.perf_counter_ns()
            ready = await run_in_threadpool(worker.warmup)
            attempts.append(
                {
                    "ready": ready,
                    "elapsed_ms": (time.perf_counter_ns() - began) / 1_000_000,
                    "worker_pid": worker.process_id,
                    "restart_count": worker.restart_count,
                }
            )
            if ready:
                break
            await run_in_threadpool(worker.close)
        return {"ready": ready, "attempts": attempts}

    app.router.routes.insert(
        0, APIRoute("/__p6/worker-preparation", prepare_worker, methods=["POST"])
    )
    return app


def timing_header(value):
    found = re.fullmatch(r"application;dur=(\d+(?:\.\d+)?)", value)
    if found is None:
        raise ValueError("缺少有效的服务端独立耗时，不能拿客户端值冒充")
    return float(found[1])


def statistics(rows):
    if not rows:
        raise ValueError("空样本不能成为性能证据")

    def percentiles(name):
        values = sorted(r[name] for r in rows if r.get(name) is not None)
        return {
            "measured_count": len(values),
            "p50_ms": values[max(0, math.ceil(len(values) * 0.5) - 1)] if values else None,
            "p95_ms": values[max(0, math.ceil(len(values) * 0.95) - 1)] if values else None,
            "max_ms": max(values) if values else None,
        }

    n = len(rows)
    failed = sum(not r["passed_correctness"] for r in rows)
    exceeded = sum(
        r.get("service_elapsed_ms") is None or r["service_elapsed_ms"] > r["budget_ms"]
        for r in rows
    )
    fallback = sum(r.get("solver_fallback", False) for r in rows)
    return {
        "sample_count": n,
        "service": percentiles("service_elapsed_ms"),
        "client": percentiles("client_elapsed_ms"),
        "failure_count": failed,
        "failure_rate": failed / n,
        "deadline_exceeded_count": exceeded,
        "deadline_exceeded_rate": exceeded / n,
        "solver_fallback_count": fallback,
        "solver_fallback_rate": fallback / n,
        "official_excellent_exceeded_count": sum(
            r["client_elapsed_ms"] >= r["excellent_client_ms"] for r in rows
        ),
        "failure_reasons": dict(Counter(r["failure_reason"] for r in rows if r["failure_reason"])),
    }


def validate_profile(config, suite, policy):
    if config["suite_hash"] != suite_hash(suite) or config["rounds"] < 3:
        raise ValueError("性能清单须绑定固定数据且至少三轮")
    if config["budgets_ms"] != {
        "INITIAL": policy.initial_budget.total_ms,
        "REPLAN": policy.replan_budget.total_ms,
    }:
        raise ValueError("性能实验不得放宽既定预算")
    if config["budgets_ms"] != {"INITIAL": 4200, "REPLAN": 2400}:
        raise ValueError("项目目标预算不可自动降级")
    if config["concurrent_jobs"] != 1 or policy.max_solver_search_workers > 4:
        raise ValueError("未授权增加在线调度资源")
    cases = {
        c["case_id"]: c
        for k in ("single_recipes", "combinations", "boundaries", "replans")
        for c in suite[k]
    }
    selected = config["case_ids"]
    if not selected or len(set(selected)) != len(selected) or not set(selected) <= set(cases):
        raise ValueError("代表菜单身份缺失或重复")
    required = {c["case_id"] for c in suite["replans"]}
    required |= {"combination-041", "combination-043", "combination-052"}
    if not required <= set(selected):
        raise ValueError("已知困难菜单和完整重排脚本必须保留")
    return [cases[c] for c in selected]


def hardware():
    script = (
        "$p6Cpu=Get-CimInstance Win32_Processor;"
        "$p6Host=Get-CimInstance Win32_ComputerSystem;"
        "@{cpu_names=@($p6Cpu.Name);logical_cpu_count=$p6Host.NumberOfLogicalProcessors;"
        "total_memory_bytes=$p6Host.TotalPhysicalMemory}|ConvertTo-Json -Compress"
    )
    measured = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return {
        **json.loads(measured.stdout),
        "os": platform.platform(),
        "python": platform.python_version(),
        "os_cpu_count": os.cpu_count(),
        "clocks": {name: vars(time.get_clock_info(name)) for name in ("monotonic", "perf_counter")},
    }


def freeze(path, value):
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with gzip.open(path, "wb", compresslevel=1) as stream:
        stream.write(encoded)
    return hashlib.sha256(path.read_bytes()).hexdigest()


class HttpMeasurements:
    def __init__(self, client, database, knowledge, output, *, policy=None):
        self.client, self.knowledge, self.output = client, knowledge, output
        self.policy = policy
        self.engine = create_engine(
            URL.create("sqlite", database=str(database)), poolclass=NullPool
        )

    def measure(self, url, body, identity, case, round_number, stage, *, headers=None):
        row = {
            "case_id": case["case_id"],
            "round": round_number,
            "stage": stage,
            "status": "FAILED",
            "passed_correctness": False,
            "budget_ms": (
                (
                    self.policy.initial_budget if stage == "INITIAL" else self.policy.replan_budget
                ).total_ms
                if self.policy is not None
                else 4200
                if stage == "INITIAL"
                else 2400
            ),
            "excellent_client_ms": 5000 if stage == "INITIAL" else 3000,
            "solver_fallback": False,
            "failure_reason": None,
        }
        artifact = {"request_url": url, "request_body": body, "request_headers": headers or {}}
        start = time.perf_counter_ns()
        try:
            response = self.client.post(url, json=body, headers=headers)
            row["client_elapsed_ms"] = (time.perf_counter_ns() - start) / 1_000_000
            artifact.update(response_body=response.text, response_headers=dict(response.headers))
            row["http_status"] = response.status_code
            row["service_elapsed_ms"] = timing_header(response.headers.get("Server-Timing", ""))
            with self.engine.connect() as tx:
                receipt = HttpRequestRepository(tx).get(identity)
                if receipt is None or receipt.result is None:
                    raise ValueError("真实 HTTP 请求未绑定持久结果")
                artifact["receipt"] = receipt.model_dump(mode="json")
                result = receipt.result
                row["status"] = result.status
                detail = result.planning
                if detail:
                    row["total_human_objective_optimized"] = detail.total_human_objective_optimized
                    row["human_objective_optimized"] = detail.human_objective_optimized
                    row["solver_fallback"] = detail.solver_fallback_used
                    row["phase_timings"] = [t.model_dump(mode="json") for t in detail.timings]
                    row["solver_stages"] = [
                        s.model_dump(mode="json", exclude={"candidate"})
                        for s in detail.stage_results
                    ]
                    row["first_validated_candidate_ms"] = detail.first_validated_candidate_ms
                if response.status_code != 200 or result.status not in {"PUBLISHED", "NO_REPLAN"}:
                    raise ValueError("HTTP 或规划未成功：" + response.text[:2000])
                if result.plan is None:
                    raise ValueError("成功响应缺少真实发布计划")
                repo = RuntimeRepository(tx)
                plan = repo.plan(receipt.session_id, result.plan.plan_version)
                problem = repo.problem(receipt.session_id, result.plan.plan_version)
                state = repo.get(receipt.session_id)
            if plan is None or plan != result.plan:
                raise ValueError("响应与持久发布不一致")
            if self.policy is not None and problem.policy != self.policy:
                raise ValueError("API 实际使用的策略与测量声明不一致")
            proof = ScheduleValidator().validate(
                self.knowledge, problem.runtime, problem, plan.validated.candidate
            )
            if not proof.valid:
                raise ValueError("成功发布未通过独立约束扫描：" + proof.model_dump_json())
            if stage == "INITIAL":
                projected = DecimalCompetitionResponse.model_validate_json(response.content)
            else:
                projected = CompetitionAdapter().to_response(plan, problem, self.knowledge)
            validate_competition_projection(
                projected, plan, problem, self.knowledge, timezone="Asia/Shanghai"
            )
            artifact.update(
                plan=plan.model_dump(mode="json"),
                problem=problem.model_dump(mode="json"),
                runtime=state.model_dump(mode="json"),
                proof=proof.model_dump(mode="json"),
                competition_response=projected.model_dump(mode="json"),
            )
            row.update(
                passed_correctness=True,
                session_id=receipt.session_id,
                problem_hash=problem.problem_hash,
                candidate_hash=plan.validated.candidate.candidate_hash,
                metrics=plan.validated.candidate.metrics.model_dump(mode="json"),
                truncation=problem.candidate_generation_report.model_dump(mode="json"),
            )
        except Exception as exc:
            row["failure_reason"] = f"{type(exc).__name__}: {exc}"
            row.setdefault("client_elapsed_ms", (time.perf_counter_ns() - start) / 1_000_000)
        path = self.output / f"round-{round_number}-{case['case_id']}-{stage.lower()}.json.gz"
        artifact["measurement"] = row
        row["artifact_sha256"] = freeze(path, artifact)
        row["artifact"] = path.name
        return row


def event_body(client, sid, script, recipes, identity):
    response = client.get(f"/api/v1/sessions/{sid}")
    response.raise_for_status()
    session = response.json()
    kind = script["event_type"]
    if kind in {"ADD_RECIPE", "REPLAY_ADD"}:
        rid = script["additional_recipe_id"]
        payload = {"recipes": [{"id": rid, "name": recipes[rid].name}]}
        kind = "ADD_RECIPE"
    else:
        payload = {
            "recipe_instance_id": session["menu"][script["target_recipe_index"]][
                "recipe_instance_id"
            ]
        }
        if kind == "DELAY_RECIPE":
            payload["earliest_start_sec"] = script["earliest_start_sec"]
    return {
        "event_id": identity,
        "event_type": kind,
        "source": session["runtime"]["execution_mode"],
        "expected_state_revision": session["runtime"]["state_revision"],
        "base_plan_version": session["runtime"]["current_plan_version"],
        "payload": payload,
    }


def run(output, *, case_ids=None):
    output.mkdir(parents=True, exist_ok=False)
    knowledge, policy = load_inputs()
    suite = json.loads((ROOT / "benchmarks/scenarios/full_suite.json").read_bytes())
    validate_suite(suite, knowledge, policy)
    config_path = ROOT / "benchmarks/scenarios/performance_profiles.json"
    config = json.loads(config_path.read_bytes())
    cases = validate_profile(config, suite, policy)
    if case_ids is not None:
        cases = [c for c in cases if c["case_id"] in case_ids]
        if {c["case_id"] for c in cases} != set(case_ids):
            raise ValueError("部分诊断只接受固定代表集内的菜单")
    recipes = {r.recipe_id.root: r for r in knowledge.recipes}
    before = source_hashes()
    helper = ROOT / "tests/fault_injection/stream_support.py"
    helper_hash = hashlib.sha256(helper.read_bytes()).hexdigest()
    started = time.perf_counter_ns()
    report = {
        "status": "RUNNING",
        "formal_acceptance": False,
        "scope": "PARTIAL_REAL_HTTP_DIAGNOSTIC" if case_ids else "WINDOWS_REAL_HTTP_THREE_ROUNDS",
        "source_hashes": before,
        "helper_hash": helper_hash,
        "profile_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        "suite_hash": suite["suite_hash"],
        "release": suite["release"],
        "policy_hash": suite["policy_hash"],
        "config": config,
        "hardware": hardware(),
        "started_at": datetime.now(UTC).isoformat(),
        "samples": [],
        "cold_startups": [],
        "api_instances": 1,
        "concurrent_jobs": 1,
        "solver_workers": 1,
        "max_search_workers": policy.max_solver_search_workers,
        "timing_scope": (
            "Server-Timing from existing HTTP middleware; full client body separately; "
            "secondary SQL reload, proof and artifact IO outside both"
        ),
        "live_llm_calls": 0,
        "worker_preparations": [],
    }
    write_json(output / "report.json", report)
    for round_number in range(1, config["rounds"] + 1):
        process = WindowsApiProcess(
            output / f"round-{round_number}-api",
            factory="benchmarks.performance:create_benchmark_app",
            environment={
                "SMART_COOKING_POLICY_PATH": str(ROOT / "data/policies/p4-runtime-v1.json")
            },
        )
        collector = None
        try:
            with closing(process.start()) as client:
                collector = HttpMeasurements(
                    client, process.directory / "runtime.sqlite3", knowledge, output
                )
                warm_case = {
                    "case_id": "excluded-warmup",
                    "recipe_ids": ["61e6c51fec6e1d65587067e1"],
                }
                warm_task = f"p6-perf-warm-{round_number}"
                warm = collector.measure(
                    f"/api/competition/plan?task_id={warm_task}",
                    [
                        {
                            "id": warm_case["recipe_ids"][0],
                            "name": recipes[warm_case["recipe_ids"][0]].name,
                        }
                    ],
                    stable_id("competition-http", warm_task, "one"),
                    warm_case,
                    round_number,
                    "INITIAL",
                    headers={"Idempotency-Key": "one"},
                )
                report["cold_startups"].append(
                    {
                        "round": round_number,
                        "startup_ms": process.last_startup_ms,
                        "api_pid": process.process.pid,
                        "excluded_warmup": warm,
                    }
                )
                if not warm["passed_correctness"]:
                    raise RuntimeError("预热请求没有完成合法发布")
                for case in cases:
                    preparation = client.post("/__p6/worker-preparation", timeout=25)
                    preparation.raise_for_status()
                    report["worker_preparations"].append(
                        {
                            "round": round_number,
                            "case_id": case["case_id"],
                            "stage": "INITIAL",
                            **preparation.json(),
                        }
                    )
                    task = f"p6-perf-{round_number}-{case['case_id']}"
                    body = [{"id": rid, "name": recipes[rid].name} for rid in case["recipe_ids"]]
                    initial = collector.measure(
                        f"/api/competition/plan?task_id={task}",
                        body,
                        stable_id("competition-http", task, "one"),
                        case,
                        round_number,
                        "INITIAL",
                        headers={"Idempotency-Key": "one"},
                    )
                    report["samples"].append(initial)
                    script = case.get("event_script")
                    if script:
                        # 初排失败也保留该菜单的重排尝试，不从分母删去。
                        with collector.engine.connect() as tx:
                            stored = HttpRequestRepository(tx).get(
                                stable_id("competition-http", task, "one")
                            )
                        if stored is None:
                            raise RuntimeError("未登记菜单使后续脚本无法执行，保留中断证据")
                        identity = f"p6-perf-change-{round_number}-{case['case_id']}"
                        change = event_body(client, stored.session_id, script, recipes, identity)
                        preparation = client.post("/__p6/worker-preparation", timeout=25)
                        preparation.raise_for_status()
                        report["worker_preparations"].append(
                            {
                                "round": round_number,
                                "case_id": case["case_id"],
                                "stage": "REPLAN",
                                **preparation.json(),
                            }
                        )
                        replanned = collector.measure(
                            f"/api/v1/sessions/{stored.session_id}/events",
                            change,
                            stable_id("internal-event", identity),
                            case,
                            round_number,
                            "REPLAN",
                        )
                        report["samples"].append(replanned)
                    print(
                        f"round={round_number} case={case['case_id']} "
                        f"rows={len(report['samples'])}",
                        flush=True,
                    )
                    write_json(output / "report.json", report)
        finally:
            if collector is not None:
                collector.engine.dispose()
            process.stop()
    report["summaries"] = {
        stage: statistics([r for r in report["samples"] if r["stage"] == stage])
        for stage in ("INITIAL", "REPLAN")
        if any(r["stage"] == stage for r in report["samples"])
    }
    report["expected_requests"] = config["rounds"] * sum(
        1 + bool(c.get("event_script")) for c in cases
    )
    report["source_unchanged"] = (
        before == source_hashes() and helper_hash == hashlib.sha256(helper.read_bytes()).hexdigest()
    )
    valid = (
        report["source_unchanged"]
        and len(report["samples"]) == report["expected_requests"]
        and all(p["ready"] for p in report["worker_preparations"])
    )
    targets = all(
        not s["failure_count"]
        and not s["deadline_exceeded_count"]
        and not s["official_excellent_exceeded_count"]
        for s in report["summaries"].values()
    )
    report["status"] = "TARGETS_MET" if valid and targets else "TARGETS_NOT_MET"
    report["elapsed_ms"] = (time.perf_counter_ns() - started) / 1_000_000
    report["finished_at"] = datetime.now(UTC).isoformat()
    write_json(output / "report.json", report)
    print(
        json.dumps(
            {"status": report["status"], "summaries": report["summaries"]}, ensure_ascii=False
        ),
        flush=True,
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--case-id", action="append")
    args = parser.parse_args()
    try:
        report = run(args.output.resolve(), case_ids=args.case_id)
    except Exception as exc:
        evidence = args.output.resolve() / "report.json"
        if evidence.is_file():
            report = json.loads(evidence.read_bytes())
            report.update(
                status="ABORTED",
                abort_reason=f"{type(exc).__name__}: {exc}",
                actual_requests=len(report.get("samples", [])),
                source_unchanged=report.get("source_hashes") == source_hashes(),
                finished_at=datetime.now(UTC).isoformat(),
            )
            write_json(evidence, report)
        raise
    return 0 if report["status"] == "TARGETS_MET" else 1


if __name__ == "__main__":
    raise SystemExit(main())
