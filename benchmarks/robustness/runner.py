"""40×100×4 配对敏感性实验；初排/缓存/真实重算/轨迹失败分别报告。"""

import argparse
import gzip
import hashlib
import json
import platform
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from app.domain.base import content_hash
from app.domain.carrier_timing import task_intervals
from app.domain.policy import SchedulingPolicy
from app.domain.ports import Deadline, PlanningRequest
from app.domain.reports import PlanningFailure, PlanningResult
from app.domain.schedule import ValidatedSchedule
from app.runtime.replanning import prepare_replan
from app.scheduling.budget import ComputationBudget
from app.scheduling.worker import SolverWorker
from app.services.planning_core import PlanningCore
from app.services.replanning import ReplanningService
from app.validation.schedule import ScheduleValidator
from benchmarks.dataset import ROOT, load_inputs, validate_suite
from benchmarks.robustness.observed_session import bind_plan, start_session
from benchmarks.robustness.simulator import FutureDurations, ObservationCache, TrajectorySimulator
from benchmarks.runner import source_hashes, write_json

CONFIG = ROOT / "benchmarks/scenarios/duration_profiles.json"


def freeze(path, value):
    # 单次 C 编码避免 json.dump 的大量微小 gzip 写入；证据内容与格式不变。
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    with gzip.open(path, "wt", encoding="utf-8", compresslevel=1) as target:
        target.write(payload)


def prewarm(worker):
    attempts = []
    for attempt in range(2):
        began = time.perf_counter_ns()
        ready = worker.warmup()
        attempts.append(
            {
                "attempt": attempt + 1,
                "ready": ready,
                "elapsed_ms": (time.perf_counter_ns() - began) / 1_000_000,
                "worker_pid": worker.process_id,
                "worker_alive": worker.is_alive,
            }
        )
        if ready:
            return True, attempts
        worker.close()
    return False, attempts


def summary(rows):
    completed = [r for r in rows if r["status"] == "COMPLETED"]
    return {
        "trajectory_count": len(rows),
        "completed": len(completed),
        "failed": len(rows) - len(completed),
        "failure_rate": (len(rows) - len(completed)) / len(rows),
        "spread_over_240_count": sum(r.get("spread_over_240") is True for r in rows),
        "spread_over_300_count": sum(r.get("spread_over_300") is True for r in rows),
        "spread_denominator": len(completed),
        "incomplete_separately_counted": len(rows) - len(completed),
        "spread_over_240_rate_among_completed": sum(r["spread_over_240"] for r in completed)
        / len(completed)
        if completed
        else None,
        "spread_over_300_rate_among_completed": sum(r["spread_over_300"] for r in completed)
        / len(completed)
        if completed
        else None,
        "delay_p50_sec": percentile([r["delay_sec"] for r in completed], 0.5),
        "delay_p95_sec": percentile([r["delay_sec"] for r in completed], 0.95),
        "delay_max_sec": max((r["delay_sec"] for r in completed), default=None),
        "dispatch_wait_total_sec": sum(r.get("dispatch_wait_sec", 0) for r in rows),
        "human_block_max_sec": max((r.get("max_human_block_sec", 0) for r in rows), default=0),
        "replans": sum(r.get("replan_count", 0) for r in rows),
        "notification_changes": sum(r.get("notification_change_count", 0) for r in rows),
        "failures": dict(Counter(r["failure"] for r in rows if r["status"] != "COMPLETED")),
        "failure_codes": dict(
            Counter(
                (r.get("failure_detail") or {}).get("code", "UNCLASSIFIED")
                for r in rows
                if r["status"] != "COMPLETED"
            )
        ),
    }


def percentile(values, fraction):
    import math

    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)] if values else None


class ObservedPlanner:
    def __init__(self, worker, previous, previous_problem, output):
        self.worker, self.previous, self.previous_problem, self.output = (
            worker,
            previous,
            previous_problem,
            output,
        )
        self.cache, self.computations = {}, []

    @staticmethod
    def cache_key(knowledge, observed):
        return hashlib.sha256(
            (content_hash(knowledge) + ":" + content_hash(observed)).encode()
        ).hexdigest()

    def __call__(self, knowledge, observed):
        began = time.perf_counter_ns()
        request_began = began
        key = self.cache_key(knowledge, observed)
        if key in self.cache:
            session, problem, prior = self.cache[key]
            return (
                session,
                problem,
                {
                    **prior,
                    "cache_hit": True,
                    "lookup_ms": (time.perf_counter_ns() - began) / 1_000_000,
                },
            )
        ready, warming = prewarm(self.worker)
        began = time.perf_counter_ns()
        spec = observed.policy.replan_budget
        deadline = Deadline(expires_at_ns=time.monotonic_ns() + spec.total_ms * 1_000_000)
        service = ReplanningService(
            knowledge,
            self.worker,
            self.previous,
            computation_budget=ComputationBudget.from_spec(spec),
        )
        result, problem, updated = None, None, None
        failure = None
        try:
            if not ready:
                raise ValueError("重排工作进程两次预热失败；未启动计算")
            request = prepare_replan(observed, None, knowledge, observed.policy)
            result = service.compute(request, deadline)
            problem = service.last_problem
            if result.status != "VALIDATED":
                failure = result.failure.message if result.failure else "重排没有完整候选"
            else:
                proof = ScheduleValidator().validate(
                    knowledge, request.runtime, problem, result.candidate
                )
                if not proof.valid:
                    failure = "重排独立扫描失败"
                elif time.monotonic_ns() >= deadline.expires_at_ns:
                    failure = "重排最终独立扫描超出固定 2400ms 预算"
                else:
                    updated = bind_plan(
                        observed,
                        problem,
                        ValidatedSchedule(candidate=result.candidate, validation=proof),
                    )
                    if time.monotonic_ns() >= deadline.expires_at_ns:
                        updated, failure = None, "离线重排绑定超出固定 2400ms 预算"
        except ValueError as exc:
            failure = str(exc)
        elapsed = (time.perf_counter_ns() - began) / 1_000_000
        changes = 0
        if updated is not None:
            old = task_intervals(self.previous_problem, self.previous.assignments)
            new = task_intervals(problem, result.candidate.assignments)
            finished = {t for e in observed.runtime.executions for t in e.completed_task_ids}
            # 个别任务开始/预计结束门槛的语义变化；不把每次版本 ID 改变算一次时间变化。
            changes = sum(2 for t, span in new.items() if t not in finished and old.get(t) != span)
        row = {
            "observed_key": key,
            "cache_hit": False,
            "lookup_ms": 0,
            "compute_elapsed_ms": elapsed,
            "observed_request_elapsed_ms": (time.perf_counter_ns() - request_began) / 1_000_000,
            "budget_ms": spec.total_ms,
            "prewarm_outside_compute_budget": warming,
            "failure": failure,
            "notification_change_count": changes,
            "input_state_revision": observed.runtime.state_revision,
            "input_plan_version": observed.runtime.current_plan_version,
            "problem_hash": problem.problem_hash if problem else None,
        }
        freeze(
            self.output / f"replan-{key}.json.gz",
            {
                "request_scope": "OBSERVED_FACTS_ONLY_NO_FUTURE_SEED",
                "observed": observed.model_dump(mode="json"),
                "problem": problem.model_dump(mode="json") if problem else None,
                "result": result.model_dump(mode="json") if result else None,
                "measurement": row,
            },
        )
        self.cache[key] = updated, problem, row
        self.computations.append(row)
        return updated, problem, row


def initial_plan(knowledge, policy, case, worker, config, origin):
    session = start_session(knowledge, policy, case, origin)
    ready, warming = prewarm(worker)
    if not ready:
        result = PlanningResult(
            status="FAILED",
            failure=PlanningFailure(
                code="NO_FEASIBLE_PLAN",
                failure_class="NO_SOLUTION_WITHIN_BUDGET",
                message="初排工作进程两次预热失败；对应全部轨迹保留为初排失败",
            ),
        )
        return None, None, result, 0, warming
    began = time.perf_counter_ns()
    core = PlanningCore(
        solver=worker, computation_budget=ComputationBudget.from_spec(policy.initial_budget)
    )
    request = PlanningRequest(
        request_id="initial-" + case["case_id"], menu=session.menu, policy=policy
    )
    result = core.compute(
        request,
        knowledge,
        session.runtime,
        Deadline(expires_at_ns=time.monotonic_ns() + policy.initial_budget.total_ms * 1_000_000),
    )
    if result.status != "VALIDATED":
        return (
            None,
            core.last_problem,
            result,
            (time.perf_counter_ns() - began) / 1_000_000,
            warming,
        )
    proof = ScheduleValidator().validate(
        knowledge, session.runtime, core.last_problem, result.candidate
    )
    if not proof.valid:
        raise ValueError("初排独立扫描失败")
    initial = bind_plan(
        session, core.last_problem, ValidatedSchedule(candidate=result.candidate, validation=proof)
    )
    return initial, core.last_problem, result, (time.perf_counter_ns() - began) / 1_000_000, warming


def run(output, *, case_ids=None, trajectories=None):
    output.mkdir(parents=True, exist_ok=False)
    config = json.loads(CONFIG.read_bytes())
    base, policy = load_inputs()
    suite = json.loads((ROOT / "benchmarks/scenarios/full_suite.json").read_bytes())
    validate_suite(suite, base, policy)
    selected = {f"combination-{i:03d}" for i in range(37)} | {
        "combination-041",
        "combination-043",
        "combination-052",
    }
    cases = [c for c in suite["combinations"] if c["case_id"] in selected]
    if case_ids is not None:
        cases = [c for c in cases if c["case_id"] in case_ids]
        if len(cases) != len(case_ids):
            raise ValueError("部分案例必须属于固定四十菜单")
    count = config["trajectories"] if trajectories is None else trajectories
    if not 1 <= count <= config["trajectories"] or not cases:
        raise ValueError("不能生成空报告或扩大未经登记的分布")
    source_before = source_hashes()
    began = time.perf_counter_ns()
    report = {
        "status": "RUNNING",
        "scope": "SYNTHETIC_OFFLINE_PAIRED_DURATION_EXPERIMENT",
        "simulator_version": "observed-start-window-v2",
        "dispatch_semantics": "Keep formal release/buffer gates and actual resource ownership; "
        "clamp the shifted plan reference to the observed dependency window",
        "formal_acceptance": False,
        "config": config,
        "config_hash": hashlib.sha256(
            json.dumps(config, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest(),
        "suite_hash": suite["suite_hash"],
        "release": base.release.model_dump(mode="json"),
        "base_policy_hash": content_hash(policy),
        "source_hashes": source_before,
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
        "started_at": datetime.now(UTC).isoformat(),
        "rows": [],
        "initial_plans": [],
        "replan_computations": [],
        "partial": case_ids is not None or count != 100,
    }
    write_json(output / "report.json", report)
    with SolverWorker() as worker:
        for case in cases:
            knowledge = base.model_copy(
                update={
                    "recipes": tuple(
                        r for r in base.recipes if r.recipe_id.root in case["recipe_ids"]
                    ),
                    "recipe_contexts": tuple(
                        c for c in base.recipe_contexts if c.recipe_id.root in case["recipe_ids"]
                    ),
                }
            )
            for duration_policy in ("NOMINAL", "BUFFERED"):
                values = policy.model_dump(mode="json")
                values.update(duration_policy_id=duration_policy)
                if duration_policy == "BUFFERED":
                    values.update(
                        duration_data_version=config["id"],
                        duration_buffer_basis_points=config["buffer_basis_points"],
                    )
                selected_policy = SchedulingPolicy.model_validate(values)
                session, problem, initial, elapsed, warming = initial_plan(
                    knowledge,
                    selected_policy,
                    case,
                    worker,
                    config,
                    datetime.fromisoformat(suite["time_origin"]),
                )
                initial_row = {
                    "case_id": case["case_id"],
                    "policy": duration_policy,
                    "status": initial.status,
                    "elapsed_ms": elapsed,
                    "policy_hash": content_hash(selected_policy),
                    "problem_hash": problem.problem_hash if problem else None,
                    "prewarm_outside_compute_budget": warming,
                }
                report["initial_plans"].append(initial_row)
                freeze(
                    output / f"initial-{case['case_id']}-{duration_policy}.json.gz",
                    {
                        "session": session.model_dump(mode="json") if session else None,
                        "problem": problem.model_dump(mode="json") if problem else None,
                        "result": initial.model_dump(mode="json"),
                        "measurement": initial_row,
                    },
                )
                planner = (
                    ObservedPlanner(worker, initial.candidate, problem, output) if session else None
                )
                observation_cache = ObservationCache()
                for trajectory in range(count):
                    for method in ("SHIFT", "REPLAN"):
                        group = duration_policy + "_" + method
                        if session is None:
                            row = {
                                "status": "FAILED",
                                "failure": "INITIAL_PLAN_FAILED: " + initial.failure.message,
                                "replan_count": 0,
                                "failure_detail": {"code": "INITIAL_PLAN_FAILED"},
                            }
                        else:
                            future = FutureDurations(
                                config["seed"], case["case_id"], trajectory, config
                            )
                            simulation = TrajectorySimulator(
                                knowledge,
                                session,
                                problem,
                                initial.candidate,
                                future,
                                config,
                                planner=planner if method == "REPLAN" else None,
                                cache=observation_cache,
                            )
                            row = simulation.run()
                        row.update(case_id=case["case_id"], trajectory=trajectory, group=group)
                        artifact = f"trajectory-{case['case_id']}-{group}-{trajectory:03d}.json.gz"
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
                if planner:
                    report["replan_computations"].extend(planner.computations)
                freeze(
                    output / f"builds-{case['case_id']}-{duration_policy}.json.gz",
                    {
                        "build_reports": [b.model_dump(mode="json") for b in worker.build_reports],
                        "all_mappings_preserved": True,
                    },
                )
                worker.build_reports.clear()  # 已绑定保存；不累计保留无界历史报告。
                initial_row["observation_transition_cache"] = {
                    "hits": observation_cache.hits,
                    "real_transitions": observation_cache.misses,
                    "entries": len(observation_cache.transitions),
                    "cached_terminal_compilations": len(observation_cache.final_problems),
                    "independent_terminal_scans_reused": False,
                }
                print(
                    json.dumps(
                        {
                            "case": case["case_id"],
                            "policy": duration_policy,
                            "rows": len(report["rows"]),
                            "replan_computations": len(report["replan_computations"]),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                write_json(output / "report.json", report)
    report["summaries"] = {
        g: summary([r for r in report["rows"] if r["group"] == g]) for g in config["groups"]
    }
    report["expected_trajectories"] = len(cases) * count * 4
    report["actual_trajectories"] = len(report["rows"])
    report["source_unchanged"] = source_before == source_hashes()
    report["elapsed_ms"] = (time.perf_counter_ns() - began) / 1_000_000
    report["finished_at"] = datetime.now(UTC).isoformat()
    report["status"] = (
        "EXPERIMENT_COMPLETED"
        if report["source_unchanged"]
        and report["actual_trajectories"] == report["expected_trajectories"]
        else "INVALID_EXPERIMENT"
    )
    report["buffered_enabled_by_default"] = False
    report["policy_decision"] = (
        "Retain NOMINAL; free-root-wait BUFFERED is an optional sensitivity control, "
        "not a measured-duration claim"
    )
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
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case-id", action="append")
    parser.add_argument("--trajectories", type=int)
    args = parser.parse_args()
    report = run(args.output, case_ids=args.case_id, trajectories=args.trajectories)
    return 0 if report["status"] == "EXPERIMENT_COMPLETED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
