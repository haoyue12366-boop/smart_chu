"""快速重排修复前后，固定菜单、预算与真实 HTTP 的局部配对实验。"""

import argparse
import gzip
import hashlib
import json
import os
from contextlib import closing
from datetime import UTC, datetime
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

from fastapi.routing import APIRoute

from app.config import ROOT
from app.domain.candidates import stable_id
from app.domain.competition_decimal import DecimalCompetitionResponse
from app.domain.policy import SchedulingPolicy
from benchmarks.dataset import load_inputs
from benchmarks.performance import (
    HttpMeasurements,
    create_benchmark_app,
    freeze,
    hardware,
    statistics,
)
from benchmarks.runner import source_hashes, write_json
from tests.fault_injection.stream_support import WindowsApiProcess

CASES = (
    "combination-000",
    "combination-018",
    "combination-041",
    "combination-043",
    "combination-052",
)


def create_time_save_benchmark_app():
    """单独实验进程使用归档的原引擎；其余模块保持当前未变的源码。"""
    if os.environ.get("SMART_COOKING_TIMESAVE_BASELINE") == "1":
        path = (
            ROOT
            / "benchmarks/reports/verification/P6-timesave-fix-run1/producers/engine-before.py.txt"
        )
        loader = SourceFileLoader("timesave_archived_engine", str(path))
        spec = spec_from_loader(loader.name, loader)
        assert spec is not None
        module = module_from_spec(spec)
        loader.exec_module(module)
        from app.scheduling import engine
        from app.services import planning_core

        engine.PlanningEngine = module.PlanningEngine
        planning_core.PlanningEngine = module.PlanningEngine
    app = create_benchmark_app()

    async def attest_engine():
        from app.services.planning_core import PlanningEngine

        path = Path(PlanningEngine.plan.__code__.co_filename)
        return {
            "module": PlanningEngine.__module__,
            "path": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }

    app.router.routes.insert(0, APIRoute("/__p6/effective-engine", attest_engine, methods=["GET"]))
    return app


def run(output: Path, label: str, rounds: int) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    knowledge, _ = load_inputs()
    recipes = {r.recipe_id.root: r for r in knowledge.recipes}
    suite = json.loads((ROOT / "benchmarks/scenarios/full_suite.json").read_bytes())
    cases = [c for c in suite["combinations"] if c["case_id"] in CASES]
    assert len(cases) == len(CASES)
    source_before = source_hashes()
    report = {
        "status": "RUNNING",
        "label": label,
        "scope": "PARTIAL_TIMESAVE_REAL_HTTP",
        "formal_acceptance": False,
        "started_at": datetime.now(UTC).isoformat(),
        "source_hashes": source_before,
        "hardware": hardware(),
        "suite_hash": suite["suite_hash"],
        "release": suite["release"],
        "rounds": rounds,
        "samples": [],
        "worker_preparations": [],
        "policies": {},
        "cold_startups": [],
        "engine_attestations": [],
        "live_llm_calls": 0,
    }
    write_json(output / "report.json", report)
    old_environment = os.environ.get("SMART_COOKING_POLICY_PATH")
    old_baseline = os.environ.get("SMART_COOKING_TIMESAVE_BASELINE")
    os.environ["SMART_COOKING_TIMESAVE_BASELINE"] = "1" if label == "before" else "0"
    try:
        for budget in (2400, 3000):
            selected = SchedulingPolicy.model_validate_json(
                (ROOT / "data/policies/p6-feedback-fast-v1.json").read_bytes()
            )
            selected = selected.model_copy(
                update={
                    "policy_version": f"timesave-comparison-v2:{budget}",
                    "replan_budget": selected.replan_budget.model_copy(
                        update={
                            "total_ms": budget,
                            "solver_ms": 1500 if budget == 2400 else 2100,
                        }
                    ),
                }
            )
            path = output / f"policy-{budget}.json"
            path.write_text(selected.model_dump_json(indent=2), encoding="utf-8")
            report["policies"][str(budget)] = {
                "policy": selected.model_dump(mode="json"),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            os.environ["SMART_COOKING_POLICY_PATH"] = str(path.resolve())
            for round_number in range(1, rounds + 1):
                directory = output / f"budget-{budget}-round-{round_number}"
                process = WindowsApiProcess(
                    directory, factory="benchmarks.time_save:create_time_save_benchmark_app"
                )
                collector = None
                try:
                    with closing(process.start()) as client:
                        attestation = client.get("/__p6/effective-engine")
                        attestation.raise_for_status()
                        report["engine_attestations"].append(attestation.json())
                        report["cold_startups"].append(
                            {
                                "budget_ms": budget,
                                "round": round_number,
                                "elapsed_ms": process.last_startup_ms,
                            }
                        )
                        prepared = client.post("/__p6/worker-preparation")
                        prepared.raise_for_status()
                        assert prepared.json()["ready"], prepared.text
                        report["worker_preparations"].append(prepared.json())
                        collector = HttpMeasurements(
                            client, directory / "runtime.sqlite3", knowledge, directory
                        )
                        for case in cases:
                            menu = [
                                {"id": rid, "name": recipes[rid].name} for rid in case["recipe_ids"]
                            ]
                            task = f"timesave-{budget}-{round_number}-{case['case_id']}"
                            url = f"/api/competition/plan?task_id={task}"
                            for stage, body, key in (
                                ("INITIAL", menu[:-1], "initial"),
                                ("REPLAN", menu[-1:], "add"),
                            ):
                                row = collector.measure(
                                    url,
                                    body,
                                    stable_id("competition-http", task, key),
                                    case,
                                    round_number,
                                    stage,
                                    headers={"Idempotency-Key": key},
                                )
                                row["budget_ms"] = 4200 if stage == "INITIAL" else budget
                                row["replan_budget_ms"] = budget
                                artifact_path = directory / row["artifact"]
                                artifact = json.loads(gzip.decompress(artifact_path.read_bytes()))
                                if row["passed_correctness"]:
                                    response = json.loads(artifact["response_body"])
                                    assert set(response) == {
                                        "overview",
                                        "cookingTimeline",
                                        "ingredientsSummary",
                                        "detailTimeline",
                                        "recipeDetail",
                                    }
                                    assert (
                                        DecimalCompetitionResponse.model_validate(
                                            response
                                        ).model_dump(mode="json")
                                        == artifact["competition_response"]
                                    )
                                    row["timeSave"] = response["overview"]["timeSave"]
                                row["artifact"] = str(artifact_path.relative_to(output))
                                artifact["measurement"] = row.copy()
                                row["artifact_sha256"] = freeze(artifact_path, artifact)
                                report["samples"].append(row)
                                write_json(output / "report.json", report)
                finally:
                    if collector is not None:
                        collector.engine.dispose()
                    process.stop()
    finally:
        if old_environment is None:
            os.environ.pop("SMART_COOKING_POLICY_PATH", None)
        else:
            os.environ["SMART_COOKING_POLICY_PATH"] = old_environment
        if old_baseline is None:
            os.environ.pop("SMART_COOKING_TIMESAVE_BASELINE", None)
        else:
            os.environ["SMART_COOKING_TIMESAVE_BASELINE"] = old_baseline
    report["source_unchanged"] = source_before == source_hashes()
    report["statistics"] = {
        str(budget): {
            stage: statistics(
                [
                    r
                    for r in report["samples"]
                    if r["replan_budget_ms"] == budget and r["stage"] == stage
                ]
            )
            for stage in ("INITIAL", "REPLAN")
        }
        for budget in (2400, 3000)
    }
    report["status"] = (
        "MEASURED_WITH_FAILURES"
        if any(not r["passed_correctness"] for r in report["samples"])
        else "PASSED"
    )
    report["finished_at"] = datetime.now(UTC).isoformat()
    write_json(output / "report.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--rounds", type=int, default=3)
    args = parser.parse_args()
    report = run(args.output, args.label, args.rounds)
    print(
        json.dumps(
            {"status": report["status"], "statistics": report["statistics"]}, ensure_ascii=False
        )
    )


if __name__ == "__main__":
    main()
