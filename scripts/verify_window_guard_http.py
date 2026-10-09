"""同菜单、3000ms、三轮单并发的真实 HTTP；可选保护开关配对。"""

import gzip
import hashlib
import json
import os
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from app.config import ROOT
from app.domain.candidates import stable_id
from app.domain.policy import SchedulingPolicy
from benchmarks.dataset import load_inputs
from benchmarks.performance import HttpMeasurements, freeze, hardware, statistics
from benchmarks.runner import source_hashes, write_json
from tests.fault_injection.stream_support import WindowsApiProcess

OUTPUT = ROOT / "benchmarks/reports/P6-dynamic-window-http-run1"


def main():
    OUTPUT.mkdir(parents=True, exist_ok=False)
    knowledge, _ = load_inputs()
    recipes = {r.recipe_id.root: r for r in knowledge.recipes}
    suite = json.loads((ROOT / "benchmarks/scenarios/full_suite.json").read_bytes())
    cases = [
        c
        for c in suite["combinations"]
        if c["case_id"] in {"combination-000", "combination-018", "combination-052"}
    ]
    assert len(cases) == 3
    original = os.environ.get("SMART_COOKING_POLICY_PATH")
    report = {
        "status": "RUNNING",
        "scope": "PARTIAL_WINDOW_GUARD_PAIRED_REAL_HTTP_36_REQUESTS",
        "formal_acceptance": False,
        "full_p6_acceptance": False,
        "started_at": datetime.now(UTC).isoformat(),
        "hardware": hardware(),
        "source_hashes": source_hashes(),
        "producer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "samples": [],
        "policies": {},
        "worker_preparations": [],
        "rounds": 3,
        "api_instances": 1,
        "concurrent_jobs": 1,
        "solver_workers": 1,
    }
    try:
        for round_number in range(1, 4):
            # 轮次交替顺序，减少启动和热状态差异造成的偏置。
            for guarded in (False, True) if round_number % 2 else (True, False):
                flag = "GUARD_ON" if guarded else "GUARD_OFF"
                selected = SchedulingPolicy.model_validate_json(
                    (ROOT / "data/policies/p6-window-guard-v1.json").read_bytes()
                )
                if not guarded:
                    selected = selected.model_copy(
                        update={
                            "dispatch_guard_policy_id": "NONE",
                            "policy_version": "p6-window-guard-control-v1",
                        }
                    )
                policy_path = OUTPUT / (flag.lower() + "-policy.json")
                policy_path.write_text(selected.model_dump_json(indent=2) + "\n", encoding="utf-8")
                report["policies"][flag] = selected.model_dump(mode="json")
                os.environ["SMART_COOKING_POLICY_PATH"] = str(policy_path)
                directory = OUTPUT / f"round-{round_number}-{flag.lower()}"
                process = WindowsApiProcess(
                    directory, factory="benchmarks.performance:create_benchmark_app"
                )
                collector = None
                try:
                    with closing(process.start()) as client:
                        prepared = client.post("/__p6/worker-preparation")
                        prepared.raise_for_status()
                        assert prepared.json()["ready"]
                        report["worker_preparations"].append(prepared.json())
                        collector = HttpMeasurements(
                            client, directory / "runtime.sqlite3", knowledge, directory
                        )
                        for case in cases:
                            menu = [
                                {"id": rid, "name": recipes[rid].name} for rid in case["recipe_ids"]
                            ]
                            task = f"window-{flag}-{round_number}-{case['case_id']}"
                            for stage, body, identity in (
                                ("INITIAL", menu[:-1], "initial"),
                                ("REPLAN", menu[-1:], "add"),
                            ):
                                row = collector.measure(
                                    f"/api/competition/plan?task_id={task}",
                                    body,
                                    stable_id("competition-http", task, identity),
                                    case,
                                    round_number,
                                    stage,
                                    headers={"Idempotency-Key": identity},
                                )
                                row.update(
                                    group=flag, budget_ms=4200 if stage == "INITIAL" else 3000
                                )
                                path = directory / row["artifact"]
                                frozen = json.loads(gzip.decompress(path.read_bytes()))
                                if row["passed_correctness"]:
                                    assert (
                                        frozen["runtime"]["policy"]["dispatch_guard_policy_id"]
                                        == "TIGHT_HUMAN_V1"
                                        if guarded
                                        else "dispatch_guard_policy_id"
                                        not in frozen["runtime"]["policy"]
                                    )
                                    response = json.loads(frozen["response_body"])
                                    assert set(response) == {
                                        "overview",
                                        "cookingTimeline",
                                        "ingredientsSummary",
                                        "detailTimeline",
                                        "recipeDetail",
                                    }
                                    row["timeSave"] = response["overview"]["timeSave"]
                                row["artifact"] = path.relative_to(OUTPUT).as_posix()
                                frozen["measurement"] = row.copy()
                                row["artifact_sha256"] = freeze(path, frozen)
                                report["samples"].append(row)
                                write_json(OUTPUT / "report.json", report)
                        print(
                            json.dumps(
                                {
                                    "round": round_number,
                                    "guard": guarded,
                                    "samples": len(report["samples"]),
                                }
                            ),
                            flush=True,
                        )
                finally:
                    if collector is not None:
                        collector.engine.dispose()
                    process.stop()
    finally:
        if original is None:
            os.environ.pop("SMART_COOKING_POLICY_PATH", None)
        else:
            os.environ["SMART_COOKING_POLICY_PATH"] = original
    report.update(
        source_unchanged=report["source_hashes"] == source_hashes(),
        finished_at=datetime.now(UTC).isoformat(),
        statistics={
            g: {
                stage: statistics(
                    [r for r in report["samples"] if r["group"] == g and r["stage"] == stage]
                )
                for stage in ("INITIAL", "REPLAN")
            }
            for g in ("GUARD_OFF", "GUARD_ON")
        },
    )
    assert len(report["samples"]) == 36 and report["source_unchanged"]
    report["status"] = (
        "PASSED"
        if all(
            r["passed_correctness"] and r["service_elapsed_ms"] <= r["budget_ms"]
            for r in report["samples"]
        )
        else "FAILED"
    )
    write_json(OUTPUT / "report.json", report)
    print(
        json.dumps(
            {"status": report["status"], "statistics": report["statistics"]}, ensure_ascii=False
        ),
        flush=True,
    )
    return 0 if report["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
