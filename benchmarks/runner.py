"""真实 Compiler/Greedy/CP-SAT/Validator/SQLite 主链评估，失败完整保留。"""

import argparse
import gzip
import hashlib
import json
import platform
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from app.api.competition_adapter import CompetitionAdapter
from app.domain.events import RuntimeEvent
from app.runtime.clock import SimulationClock
from app.runtime.service import RuntimeService
from app.scheduling.json_worker import JsonSolverWorker
from app.services.planning import PlanningService
from app.storage.repositories import RuntimeRepository
from app.storage.unit_of_work import UnitOfWork
from app.validation.competition_contract import validate_competition_projection
from app.validation.schedule import ScheduleValidator
from benchmarks.dataset import ROOT, load_inputs, validate_suite
from benchmarks.runtime_replanning import summary


def source_hashes():
    paths = [ROOT / "pyproject.toml", ROOT / "uv.lock"]
    paths += list((ROOT / "app").rglob("*.py"))
    paths += [
        p
        for p in (ROOT / "benchmarks").rglob("*.py")
        if p.relative_to(ROOT / "benchmarks").parts[0] != "reports"
    ]
    paths += list((ROOT / "benchmarks/scenarios").rglob("*.json"))
    return {
        p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(paths)
    }


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class BenchmarkRunner:
    def run(self, suite, release, policy, seed, output, *, cases=None):
        validate_suite(suite, release, policy)
        if seed != suite["seed"]:
            raise ValueError("运行种子必须与清单一致")
        output = Path(output)
        output.mkdir(parents=True, exist_ok=False)
        selected = (
            cases
            if cases is not None
            else [
                c
                for name in ("single_recipes", "combinations", "boundaries", "replans")
                for c in suite[name]
            ]
        )
        if not selected:
            raise ValueError("不得用空运行生成验收报告")
        started = time.perf_counter_ns()
        before = source_hashes()
        report = {
            "schema_version": "1.0",
            "status": "RUNNING",
            "scope": ("PARTIAL_" if cases is not None else "FULL_")
            + release.release.release_kind.upper(),
            "formal_acceptance": False,
            "suite_hash": suite["suite_hash"],
            "release": suite["release"],
            "policy_hash": suite["policy_hash"],
            "seed": suite["seed"],
            "time_origin": suite["time_origin"],
            "started_at": datetime.now(UTC).isoformat(),
            "environment": {
                "platform": platform.platform(),
                "python": sys.version,
                "solver_workers": policy.max_solver_search_workers,
                "concurrent_jobs": 1,
            },
            "source_hashes": before,
            "samples": [],
            "case_results": [],
            "attempted_cases": 0,
            "failure_count": 0,
            "worker_preparations": [],
            "supplementary_regressions": {
                "status": "NOT_EXECUTED_BY_THIS_RUN",
                "tests": suite["supplementary_regressions"],
            },
        }
        write_json(output / "suite.json", suite)
        store = UnitOfWork(output / "runtime.sqlite3")
        store.migrate()
        worker = JsonSolverWorker()
        warm_started = time.perf_counter_ns()
        try:
            if not worker.warmup():
                raise TimeoutError("评估常驻求解进程预热失败")
            report["cold_worker_warmup_ms"] = (time.perf_counter_ns() - warm_started) / 1_000_000
            for index, case in enumerate(selected):
                warm_started = time.perf_counter_ns()
                ready = worker.warmup()
                report["worker_preparations"].append(
                    {
                        "case_id": case["case_id"],
                        "ready": ready,
                        "elapsed_ms": (time.perf_counter_ns() - warm_started) / 1_000_000,
                        "worker_pid": worker.process_id,
                    }
                )
                if not ready:
                    raise TimeoutError("案例前求解进程未就绪；保留部分失败报告")
                rows = self._case(case, index, suite, release, policy, store, worker, output)
                report["samples"].extend(rows)
                passed = all(
                    r["status"] in ("PUBLISHED", "NO_REPLAN")
                    and r["constraint_validation"]
                    and r["interface_validation"]
                    for r in rows
                )
                report["case_results"].append(
                    {
                        "case_id": case["case_id"],
                        "category": case["category"],
                        "passed": passed,
                        "request_count": len(rows),
                    }
                )
                report["attempted_cases"] += 1
                report["failure_count"] += not passed
                write_json(
                    output / "progress.json",
                    {k: v for k, v in report.items() if k not in ("source_hashes", "samples")},
                )
                print(
                    f"{index + 1}/{len(selected)} {case['case_id']} {'PASS' if passed else 'FAIL'}",
                    flush=True,
                )
        finally:
            worker.close()
            store.close()
            report["source_unchanged"] = before == source_hashes()
            report["elapsed_ms"] = (time.perf_counter_ns() - started) / 1_000_000
            report["completed_at"] = datetime.now(UTC).isoformat()
            report["status"] = (
                "PASSED"
                if report["attempted_cases"] == len(selected)
                and report["failure_count"] == 0
                and report["source_unchanged"]
                else "FAILED"
            )
            for kind in ("INITIAL", "REPLAN", "REPLAY"):
                rows = [r for r in report["samples"] if r["stage"] == kind]
                report[kind.lower() + "_summary"] = summary(rows) if rows else None
                if rows:
                    report[kind.lower() + "_summary"]["failure_count"] = sum(
                        r["status"] not in ("PUBLISHED", "NO_REPLAN") for r in rows
                    )
            report["failure_reasons"] = dict(
                Counter(r["failure_reason"] for r in report["samples"] if r["failure_reason"])
            )
            report["report_scope_note"] = (
                "清单主链运行结果；正式数据、补充设备/运行中边界、三轮性能和公网联调分别验收。"
            )
            write_json(output / "report.json", report)
        return report

    def _case(self, case, index, suite, knowledge, policy, store, worker, output):
        sid = f"p6-{case['case_id']}"
        origin = datetime.fromisoformat(suite["time_origin"])
        runtime = RuntimeService(store, knowledge, SimulationClock(origin))
        runtime.create_session(sid, "SIMULATED", origin, policy)
        planner = PlanningService(runtime, worker)
        recipes = {r.recipe_id.root: r for r in knowledge.recipes}

        def request(kind, payload, identity):
            state = runtime.get(sid).runtime
            return RuntimeEvent(
                event_id=identity,
                session_id=sid,
                event_type=kind,
                occurred_at=origin,
                received_at=origin,
                source="SIMULATED",
                expected_state_revision=state.state_revision,
                base_plan_version=state.current_plan_version,
                payload=payload,
            )

        def menu(ids):
            return {"recipes": [{"id": i, "name": recipes[i].name} for i in ids]}

        rows = [
            self._measure(
                planner,
                runtime,
                request("START_SESSION", menu(case["recipe_ids"]), sid + "-initial"),
                case,
                "INITIAL",
                output,
            )
        ]
        script = case.get("event_script")
        if script:
            current = runtime.get(sid)
            kind = script["event_type"]
            if kind in ("ADD_RECIPE", "REPLAY_ADD"):
                payload = menu([script["additional_recipe_id"]])
            else:
                target = current.menu[script["target_recipe_index"]].recipe_instance_id
                payload = {"recipe_instance_id": target}
                if kind == "DELAY_RECIPE":
                    payload["earliest_start_sec"] = script["earliest_start_sec"]
            change = request(
                "ADD_RECIPE" if kind == "REPLAY_ADD" else kind, payload, sid + "-change"
            )
            rows.append(self._measure(planner, runtime, change, case, "REPLAN", output))
            if kind == "REPLAY_ADD":
                before = runtime.get(sid)
                rows.append(self._measure(planner, runtime, change, case, "REPLAY", output))
                after = runtime.get(sid)
                # 原请求未发布时，同身份重试允许完成持久待重排；业务事实仍只应用一次。
                unchanged = (
                    after.menu == before.menu
                    and after.runtime.state_revision == before.runtime.state_revision
                    and after.runtime.executions == before.runtime.executions
                    and after.runtime.material_lots == before.runtime.material_lots
                    and after.runtime.device_states == before.runtime.device_states
                    and after.runtime.event_refs == before.runtime.event_refs
                )
                if not unchanged:
                    rows[-1].update(status="FAILED", failure_reason="同身份重放修改运行事实")
        return rows

    def _measure(self, planner, runtime, request, case, stage, output):
        result = None
        row = {
            "case_id": case["case_id"],
            "category": case["category"],
            "stage": stage,
            "status": "FAILED",
            "constraint_validation": False,
            "interface_validation": False,
            "failure_reason": None,
            "solver_fallback": False,
            "fallback_reasons": [],
            "budget_ms": runtime.get(request.session_id.root).policy.initial_budget.total_ms
            if stage == "INITIAL"
            else runtime.get(request.session_id.root).policy.replan_budget.total_ms,
        }
        artifact = {"request": request.model_dump(mode="json"), "case": case}
        started = time.perf_counter_ns()
        try:
            result = planner.apply_event(request)
            row["service_elapsed_ms"] = (time.perf_counter_ns() - started) / 1_000_000
            row["status"] = result.status
            detail = result.planning
            row["solver_fallback"] = bool(detail and detail.solver_fallback_used)
            artifact["result"] = result.model_dump(mode="json")
            if detail:
                row["first_validated_candidate_ms"] = detail.first_validated_candidate_ms
                row["phase_timings"] = [t.model_dump(mode="json") for t in detail.timings]
                if detail.failure:
                    row["failure_reason"] = detail.failure.model_dump_json()
            if result.status in ("PUBLISHED", "NO_REPLAN") and result.plan is not None:
                with runtime.store.engine.connect() as tx:
                    repo = RuntimeRepository(tx)
                    plan = repo.plan(request.session_id.root, result.plan.plan_version)
                    problem = repo.problem(request.session_id.root, result.plan.plan_version)
                if plan != result.plan:
                    raise ValueError("服务结果与真实持久发布不一致")
                proof = ScheduleValidator().validate(
                    runtime.knowledge, problem.runtime, problem, plan.validated.candidate
                )
                if not proof.valid:
                    raise ValueError("独立约束扫描拒绝已提交计划：" + proof.model_dump_json())
                response = CompetitionAdapter().to_response(plan, problem, runtime.knowledge)
                validate_competition_projection(
                    response, plan, problem, runtime.knowledge, timezone="Asia/Shanghai"
                )
                row.update(
                    constraint_validation=True,
                    interface_validation=True,
                    recipe_count=len(problem.recipe_instances),
                    problem_hash=problem.problem_hash,
                    plan_version=plan.plan_version,
                    state_revision=plan.state_revision,
                    metrics=plan.validated.candidate.metrics.model_dump(mode="json"),
                    truncation=problem.candidate_generation_report.model_dump(mode="json"),
                )
                artifact.update(
                    problem=problem.model_dump(mode="json"),
                    response=response.model_dump(mode="json"),
                    proof=proof.model_dump(mode="json"),
                )
            else:
                row["failure_reason"] = row["failure_reason"] or result.model_dump_json(
                    exclude={"plan"}
                )
        except Exception as exc:
            row.update(status="FAILED", failure_reason=f"{type(exc).__name__}: {exc}")
            artifact["exception"] = row["failure_reason"]
        row.setdefault("service_elapsed_ms", (time.perf_counter_ns() - started) / 1_000_000)
        artifact["runtime_after"] = runtime.get(request.session_id.root).model_dump(mode="json")
        target = output / f"{case['case_id']}-{stage.lower()}.json.gz"
        with gzip.open(target, "wt", encoding="utf-8") as stream:
            json.dump(artifact, stream, ensure_ascii=False)
        row["artifact_path"] = target.name
        row["artifact_sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
        return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=ROOT / "benchmarks/scenarios/full_suite.json")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--case-id", action="append", help="显式部分诊断；不会标为全量验收")
    args = parser.parse_args()
    knowledge, policy = load_inputs()
    suite = json.loads(args.suite.read_bytes())
    cases = None
    if args.case_id:
        cases = [
            c
            for name in ("single_recipes", "combinations", "boundaries", "replans")
            for c in suite[name]
            if c["case_id"] in args.case_id
        ]
        if {c["case_id"] for c in cases} != set(args.case_id):
            raise ValueError("部分诊断引用了不存在的场景")
    report = BenchmarkRunner().run(
        suite, knowledge, policy, suite["seed"], args.output, cases=cases
    )
    print(
        json.dumps(
            {
                k: report[k]
                for k in ("status", "scope", "attempted_cases", "failure_count", "elapsed_ms")
            },
            ensure_ascii=False,
        )
    )
    raise SystemExit(0 if report["status"] == "PASSED" else 1)


if __name__ == "__main__":
    main()
