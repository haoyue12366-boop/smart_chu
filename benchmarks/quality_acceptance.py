"""相同固定菜单三轮真实 HTTP；质量修复与已归档源码可分别运行。"""

import argparse
import hashlib
import json
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from app.config import ROOT, AppSettings
from app.domain.base import content_hash
from app.domain.candidates import stable_id
from benchmarks.dataset import load_inputs, suite_hash, validate_suite
from benchmarks.performance import (
    HttpMeasurements,
    event_body,
    hardware,
    statistics,
    validate_profile,
)
from benchmarks.runner import source_hashes, write_json
from tests.fault_injection.stream_support import WindowsApiProcess


def run(
    output: Path,
    *,
    case_ids: list[str] | None = None,
    before_source: Path | None = None,
    require_five_minute: bool = False,
    policy_path: Path | None = None,
):
    output.mkdir(parents=True, exist_ok=False)
    source = before_source or ROOT
    policy_path = policy_path or (
        source / "data/policies/p4-runtime-v1.json" if before_source else AppSettings().policy_path
    )
    settings = AppSettings(policy_path=policy_path)
    knowledge, policy = load_inputs(settings)
    old_knowledge, old_policy = load_inputs()
    suite = json.loads((ROOT / "benchmarks/scenarios/full_suite.json").read_bytes())
    validate_suite(suite, old_knowledge, old_policy)
    profile = json.loads((ROOT / "benchmarks/scenarios/performance_profiles.json").read_bytes())
    cases = validate_profile(profile, suite, old_policy)
    if case_ids:
        cases = [c for c in cases if c["case_id"] in case_ids]
        if {c["case_id"] for c in cases} != set(case_ids):
            raise ValueError("只接受固定代表集内的菜单")
    actual_suite = {
        **suite,
        "policy_hash": content_hash(policy),
        "policy": policy.model_dump(mode="json"),
    }
    actual_suite["suite_hash"] = suite_hash(actual_suite)
    validate_suite(actual_suite, knowledge, policy)
    write_json(output / "effective-suite.json", actual_suite)
    measured_hashes = (
        source_hashes()
        if before_source is None
        else {
            str(p.relative_to(source)).replace("\\", "/"): hashlib.sha256(
                p.read_bytes()
            ).hexdigest()
            for folder in ("app", "benchmarks")
            for p in (source / folder).rglob("*.py")
        }
    )
    observer_hashes = source_hashes()
    report = {
        "status": "RUNNING",
        "formal_acceptance": False,
        "started_at": datetime.now(UTC).isoformat(),
        "hardware": hardware(),
        "source_root": str(source),
        "source_hashes": measured_hashes,
        "observer_source_hashes": observer_hashes,
        "original_suite_hash": suite["suite_hash"],
        "suite_hash": actual_suite["suite_hash"],
        "release": knowledge.release.model_dump(mode="json"),
        "policy": policy.model_dump(mode="json"),
        "policy_hash": content_hash(policy),
        "rounds": 3,
        "expected_requests": 3 * sum(1 + bool(c.get("event_script")) for c in cases),
        "require_five_minute": require_five_minute,
        "concurrent_jobs": 1,
        "solver_workers": 1,
        "search_workers": policy.max_solver_search_workers,
        "live_llm_calls": 0,
        "scope": "FIXED_REPRESENTATIVE_MENUS_NOT_ALL_MENUS",
        "samples": [],
        "cold_startups": [],
        "worker_preparations": [],
        "timing_scope": "完整 HTTP 响应；冷启动和显式预热单列，独立复核及归档在计时之外",
    }
    recipes = {r.recipe_id.root: r for r in knowledge.recipes}
    write_json(output / "report.json", report)
    try:
        for round_number in range(1, 4):
            process = WindowsApiProcess(
                output / f"round-{round_number}-api",
                factory="benchmarks.performance:create_benchmark_app",
                source_root=source,
                environment={
                    "SMART_COOKING_POLICY_PATH": str(policy_path),
                    "SMART_COOKING_RELEASE_ROOT": str(settings.release_root),
                    "PYTHONPATH": str(ROOT),
                },
            )
            collector = None
            try:
                with closing(process.start()) as client:
                    report["cold_startups"].append(
                        {"round": round_number, "elapsed_ms": process.last_startup_ms}
                    )
                    collector = HttpMeasurements(
                        client,
                        process.directory / "runtime.sqlite3",
                        knowledge,
                        output,
                        policy=policy,
                    )
                    for case in [
                        {"case_id": "excluded-warmup", "recipe_ids": ["61e6c51fec6e1d65587067e1"]},
                        *cases,
                    ]:
                        prep = client.post("/__p6/worker-preparation", timeout=25)
                        prep.raise_for_status()
                        report["worker_preparations"].append(
                            {"round": round_number, "case_id": case["case_id"], **prep.json()}
                        )
                        task = f"quality-{round_number}-{case['case_id']}"
                        initial = collector.measure(
                            f"/api/competition/plan?task_id={task}",
                            [{"id": rid, "name": recipes[rid].name} for rid in case["recipe_ids"]],
                            stable_id("competition-http", task, "one"),
                            case,
                            round_number,
                            "INITIAL",
                            headers={"Idempotency-Key": "one"},
                        )
                        if case["case_id"] == "excluded-warmup":
                            report["cold_startups"][-1]["excluded_warmup"] = initial
                            if not initial["passed_correctness"]:
                                raise RuntimeError("预热发布失败，保留中断证据")
                            continue
                        report["samples"].append(initial)
                        if case.get("event_script"):
                            if not initial.get("session_id"):
                                raise RuntimeError("初排失败，重排不能独立执行；失败保留在分母")
                            identity = f"quality-change-{round_number}-{case['case_id']}"
                            change = event_body(
                                client,
                                initial["session_id"],
                                case["event_script"],
                                recipes,
                                identity,
                            )
                            row = collector.measure(
                                f"/api/v1/sessions/{initial['session_id']}/events",
                                change,
                                stable_id("internal-event", identity),
                                case,
                                round_number,
                                "REPLAN",
                            )
                            report["samples"].append(row)
                        print(
                            json.dumps(
                                {
                                    "round": round_number,
                                    "case": case["case_id"],
                                    "initial_ok": initial["passed_correctness"],
                                    "initial_ms": initial["client_elapsed_ms"],
                                    "spread": initial.get("metrics", {}).get(
                                        "completion_spread_sec"
                                    ),
                                    "rows": len(report["samples"]),
                                },
                                ensure_ascii=False,
                            ),
                            flush=True,
                        )
                        write_json(output / "report.json", report)
            finally:
                if collector:
                    collector.engine.dispose()
                process.stop()
        report["summaries"] = {
            stage: {
                **statistics(rows),
                "client_deadline_exceeded_count": sum(
                    r["client_elapsed_ms"] > r["budget_ms"] for r in rows
                ),
                "five_minute_count": sum(
                    r.get("metrics", {}).get("completion_spread_sec", 10**18) <= 300 for r in rows
                ),
            }
            for stage in ("INITIAL", "REPLAN")
            if (rows := [r for r in report["samples"] if r["stage"] == stage])
        }
        report["source_unchanged"] = observer_hashes == source_hashes()
        passed = (
            report["source_unchanged"] and len(report["samples"]) == report["expected_requests"]
        )
        passed = passed and all(
            not s["failure_count"]
            and not s["deadline_exceeded_count"]
            and not s["client_deadline_exceeded_count"]
            for s in report["summaries"].values()
        )
        report["all_five_minute"] = all(
            s["five_minute_count"] == s["sample_count"] for s in report["summaries"].values()
        )
        if require_five_minute:
            passed = passed and report["all_five_minute"]
        report["status"] = "CORRECT_AND_WITHIN_BUDGET" if passed else "TARGETS_NOT_MET"
    except Exception as error:
        report.update(status="ABORTED", error=f"{type(error).__name__}: {error}")
        raise
    finally:
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
    parser.add_argument("--before-source", type=Path)
    parser.add_argument("--require-five-minute", action="store_true")
    parser.add_argument("--policy-path", type=Path, help="独立测试指定版本策略，不修改在线默认策略")
    args = parser.parse_args()
    report = run(
        args.output.resolve(),
        case_ids=args.case_id,
        before_source=args.before_source.resolve() if args.before_source else None,
        require_five_minute=args.require_five_minute,
        policy_path=args.policy_path.resolve() if args.policy_path else None,
    )
    return 0 if report["status"] == "CORRECT_AND_WITHIN_BUDGET" else 1


if __name__ == "__main__":
    raise SystemExit(main())
