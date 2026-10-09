"""保留原 120 条身份，当前版本同预算固定初排的动态窗口局部复验。"""

import argparse
import gzip
import hashlib
import importlib.util
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from app.compiler.compiler import ProblemCompiler
from app.domain.base import content_hash
from app.domain.ports import Deadline
from app.domain.reports import PlanningResult
from app.domain.runtime_session import RuntimeSession
from app.domain.schedule import ValidatedSchedule
from app.domain.scheduling_problem import SchedulingProblem
from app.scheduling.metrics import compute_metrics
from app.scheduling.worker import SolverWorker
from app.validation.schedule import ScheduleValidator
from benchmarks.dataset import ROOT, load_inputs
from benchmarks.performance import hardware
from benchmarks.robustness.feedback_cache import BoundedObservationCache
from benchmarks.robustness.feedback_planner import FeedbackObservedPlanner
from benchmarks.robustness.observed_session import bind_plan, start_session
from benchmarks.robustness.runner import CONFIG, freeze, summary
from benchmarks.robustness.simulator import FutureDurations, TrajectorySimulator
from benchmarks.runner import source_hashes, write_json

HISTORY = ROOT / "benchmarks/reports/P6-feedback-critical-small-run2/report.json"
GROUPS = ("NOMINAL_SHIFT", "NOMINAL_REPLAN", "BUFFERED_SHIFT", "BUFFERED_REPLAN")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def simulator_class(legacy_source: Path | None):
    if legacy_source is None:
        path = ROOT / "benchmarks/robustness/simulator.py"
        return TrajectorySimulator, {
            "path": path.relative_to(ROOT).as_posix(),
            "sha256": digest(path),
        }
    path = legacy_source.resolve()
    evidence = ROOT / "benchmarks/reports/verification/P6-dynamic-window-fix-run1"
    expected_path = evidence / "before-source/benchmarks/robustness/simulator.py"
    manifest = json.loads((evidence / "baseline-source.json").read_bytes())
    if (
        path != expected_path.resolve()
        or digest(path) != manifest["source_hashes"]["benchmarks/robustness/simulator.py"]
    ):
        raise ValueError("对照必须实际加载修复前已归档且哈希相符的模拟器")
    spec = importlib.util.spec_from_file_location("window_repair_legacy_simulator", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.TrajectorySimulator, {
        "path": path.relative_to(ROOT).as_posix(),
        "sha256": digest(path),
    }


def run(
    output: Path,
    revision: str,
    cases: list[str],
    trajectories: int,
    guard: bool = False,
    legacy_source: Path | None = None,
) -> dict:
    assert set(cases) <= {"combination-000", "combination-018", "combination-052"}
    assert len(cases) == len(set(cases)) and 1 <= trajectories <= 10
    assert not (guard and legacy_source), "旧模拟器不支持新保护策略"
    simulator, simulator_source = simulator_class(legacy_source)
    output.mkdir(parents=True, exist_ok=False)
    base, _ = load_inputs()
    config = json.loads(CONFIG.read_bytes())
    original = json.loads(HISTORY.read_bytes())
    began, before = time.perf_counter_ns(), source_hashes()
    report = {
        "status": "RUNNING",
        "revision": revision,
        "dispatch_guard_policy_id": "TIGHT_HUMAN_V1" if guard else "NONE",
        "simulator_source": simulator_source,
        "partial": True,
        "formal_acceptance": False,
        "scope": "FIXED_120_IDENTITIES_CURRENT_CODE_3000MS",
        "started_at": datetime.now(UTC).isoformat(),
        "hardware": hardware(),
        "source_hashes": before,
        "history_sha256": digest(HISTORY),
        "release": base.release.model_dump(mode="json"),
        "config": config,
        "rows": [],
        "initials": [],
        "replan_computations": [],
    }
    assert report["release"] == original["release"]
    write_json(output / "report.json", report)
    with SolverWorker() as worker:
        for case in cases:
            for duration in ("NOMINAL", "BUFFERED"):
                path = HISTORY.parent / f"initial-{case}-{duration}.json.gz"
                frozen = json.loads(gzip.decompress(path.read_bytes()))
                old = RuntimeSession.model_validate(frozen["session"])
                selected = old.policy.model_copy(
                    update={
                        "policy_version": old.policy.policy_version + ":window-repair-3000-v1",
                        "replan_budget": old.policy.replan_budget.model_copy(
                            update={"total_ms": 3000, "solver_ms": 2100}
                        ),
                    }
                )
                if guard:
                    selected = selected.model_copy(
                        update={
                            "dispatch_guard_policy_id": "TIGHT_HUMAN_V1",
                            "policy_version": selected.policy_version + ":tight-human-v1",
                        }
                    )
                ids = {i.recipe_id for i in old.menu}
                knowledge = base.model_copy(
                    update={
                        "recipes": tuple(r for r in base.recipes if r.recipe_id in ids),
                        "recipe_contexts": tuple(
                            c for c in base.recipe_contexts if c.recipe_id in ids
                        ),
                    }
                )
                session = start_session(
                    knowledge,
                    selected,
                    {"case_id": case, "recipe_ids": [i.recipe_id.root for i in old.menu]},
                    old.runtime.time_origin.start_at,
                )
                problem = ProblemCompiler().compile(
                    knowledge,
                    session.menu,
                    session.runtime,
                    selected,
                    Deadline(expires_at_ns=time.monotonic_ns() + 4_200_000_000),
                )
                assert isinstance(problem, SchedulingProblem), problem
                candidate = PlanningResult.model_validate(frozen["result"]).candidate
                assert candidate is not None
                candidate = candidate.model_copy(update={"problem_hash": problem.problem_hash})
                candidate = candidate.model_copy(
                    update={"metrics": compute_metrics(candidate, problem)}
                )
                proof = ScheduleValidator().validate(knowledge, session.runtime, problem, candidate)
                assert proof.valid, proof
                session = bind_plan(
                    session, problem, ValidatedSchedule(candidate=candidate, validation=proof)
                )
                initial_name = f"initial-{case}-{duration}.json.gz"
                freeze(
                    output / initial_name,
                    {
                        "session": session.model_dump(mode="json"),
                        "problem": problem.model_dump(mode="json"),
                        "candidate": candidate.model_dump(mode="json"),
                        "validation": proof.model_dump(mode="json"),
                        "source_initial_sha256": digest(path),
                    },
                )
                report["initials"].append(
                    {"artifact": initial_name, "sha256": digest(output / initial_name)}
                )
                planner = FeedbackObservedPlanner(worker, candidate, problem, output, session)
                cache = BoundedObservationCache()
                for trajectory in range(trajectories):
                    for method in ("SHIFT", "REPLAN"):
                        group = duration + "_" + method
                        row = simulator(
                            knowledge,
                            session,
                            problem,
                            candidate,
                            FutureDurations(config["seed"], case, trajectory, config),
                            config,
                            planner=planner if method == "REPLAN" else None,
                            cache=cache,
                        ).run()
                        row.update(
                            case_id=case,
                            trajectory=trajectory,
                            group=group,
                            initial_candidate_hash=content_hash(candidate),
                        )
                        name = f"trajectory-{case}-{group}-{trajectory:03d}.json.gz"
                        freeze(output / name, row)
                        excluded = {"events", "final_session", "validation", "dispatch_diagnostics"}
                        report["rows"].append(
                            {k: v for k, v in row.items() if k not in excluded}
                            | {
                                "artifact": name,
                                "sha256": digest(output / name),
                            }
                        )
                report["replan_computations"].extend(planner.computations)
                print(
                    json.dumps({"case": case, "duration": duration, "rows": len(report["rows"])}),
                    flush=True,
                )
                write_json(output / "report.json", report)
    report.update(
        status="EXPERIMENT_COMPLETED",
        source_unchanged=before == source_hashes(),
        history_unchanged=digest(HISTORY) == report["history_sha256"],
        elapsed_ms=(time.perf_counter_ns() - began) / 1_000_000,
        finished_at=datetime.now(UTC).isoformat(),
        summaries={g: summary([r for r in report["rows"] if r["group"] == g]) for g in GROUPS},
    )
    assert len(report["rows"]) == len(cases) * trajectories * 4
    assert report["source_unchanged"] and report["history_unchanged"]
    write_json(output / "report.json", report)
    print(
        json.dumps(
            {"status": report["status"], "summaries": report["summaries"]}, ensure_ascii=False
        ),
        flush=True,
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--case-id", action="append")
    parser.add_argument("--trajectories", type=int, default=10)
    parser.add_argument("--guard", action="store_true")
    parser.add_argument("--legacy-source", type=Path)
    args = parser.parse_args()
    run(
        args.output,
        args.revision,
        args.case_id or ["combination-000", "combination-018", "combination-052"],
        args.trajectories,
        args.guard,
        args.legacy_source,
    )


if __name__ == "__main__":
    main()
