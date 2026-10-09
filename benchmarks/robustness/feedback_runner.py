"""同一修正模拟器比较原四组、持续反馈与显式串行菜谱保护。"""

import argparse
import gzip
import hashlib
import json
import shutil
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from app.compiler.compiler import ProblemCompiler
from app.domain.base import content_hash
from app.domain.knowledge import MenuKnowledgeView
from app.domain.policy import SchedulingPolicy
from app.domain.ports import Deadline
from app.domain.reports import PlanningResult
from app.domain.runtime_session import RuntimeSession
from app.domain.schedule import ValidatedSchedule
from app.domain.scheduling_problem import SchedulingProblem
from app.scheduling.metrics import compute_metrics
from app.scheduling.worker import SolverWorker
from app.validation.schedule import ScheduleValidator
from benchmarks.dataset import ROOT, load_inputs, validate_suite
from benchmarks.robustness.feedback import ContinuousTrajectorySimulator
from benchmarks.robustness.feedback_cache import BoundedObservationCache
from benchmarks.robustness.feedback_planner import FeedbackObservedPlanner
from benchmarks.robustness.observed_session import bind_plan, start_session
from benchmarks.robustness.runner import CONFIG, freeze, initial_plan, summary
from benchmarks.robustness.simulator import FutureDurations, TrajectorySimulator
from benchmarks.runner import source_hashes, write_json

GROUPS = (
    "NOMINAL_SHIFT",
    "NOMINAL_REPLAN",
    "BUFFERED_SHIFT",
    "BUFFERED_REPLAN",
    "NOMINAL_FAST_REPLAN",
    "NOMINAL_FAST_CONTINUOUS",
    "CRITICAL_CONTINUOUS",
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fast_initial(
    knowledge: MenuKnowledgeView,
    base_policy: SchedulingPolicy,
    case: dict[str, object],
    original: Path,
    origin: datetime,
) -> tuple[RuntimeSession | None, SchedulingProblem | None, PlanningResult, dict[str, object]]:
    began = time.perf_counter_ns()
    path = original.parent / f"initial-{case['case_id']}-NOMINAL.json.gz"
    frozen = json.loads(gzip.decompress(path.read_bytes()))
    result = PlanningResult.model_validate(frozen["result"])
    row = {
        "case_id": case["case_id"],
        "policy": "NOMINAL_FAST",
        "status": result.status,
        "source": "ARCHIVED_ASSIGNMENTS_CURRENT_FAST_POLICY_COMPILED_AND_VALIDATED",
        "original_sha256": digest(path),
        "native_initial_solve": False,
    }
    if result.candidate is None:
        return None, None, result, row
    values = base_policy.model_dump(mode="json")
    values.update(
        replan_search_mode="FEASIBILITY_FIRST",
        policy_version=base_policy.policy_version + ":feasibility-replan-v1",
    )
    selected = SchedulingPolicy.model_validate(values)
    session = start_session(knowledge, selected, case, origin)
    deadline = Deadline(expires_at_ns=time.monotonic_ns() + 4_200_000_000)
    problem = ProblemCompiler().compile(
        knowledge, session.menu, session.runtime, selected, deadline
    )
    if not isinstance(problem, SchedulingProblem):
        raise ValueError("快速对照初排重编译失败：" + problem.message)
    candidate = result.candidate.model_copy(update={"problem_hash": problem.problem_hash})
    candidate = candidate.model_copy(update={"metrics": compute_metrics(candidate, problem)})
    proof = ScheduleValidator().validate(knowledge, problem.runtime, problem, candidate)
    if not proof.valid or time.monotonic_ns() >= deadline.expires_at_ns:
        raise ValueError("快速对照原初排几何不满足当前独立校验或预算")
    session = bind_plan(session, problem, ValidatedSchedule(candidate=candidate, validation=proof))
    row["elapsed_ms"] = (time.perf_counter_ns() - began) / 1_000_000
    result = PlanningResult(status="VALIDATED", candidate=candidate, validation=proof)
    return session, problem, result, row


def run(output: Path, case_ids: list[str] | None, trajectories: int) -> dict[str, object]:
    output.mkdir(parents=True, exist_ok=False)
    config = json.loads(CONFIG.read_bytes())
    if not 1 <= trajectories <= 100:
        raise ValueError("轨迹数量必须为 1..100")
    config.update(
        feedback_extension_sec=30,
        observation_cache_max_entries=256,
        plan_cache_max_entries=16,
        cold_cache_source="Exact frozen observed-state computation, SHA256 checked before replay",
        feedback_distribution=(
            "At visible predicted end, if still RUNNING, report a fixed 30s synthetic estimate; "
            "no private remaining duration"
        ),
        groups=list(GROUPS),
        continuous_trigger=(
            "Production replan_reasons on every start/completion/duration observation; "
            "coalesce same-time facts before computing"
        ),
        critical_window_policy=(
            "SERIAL_RECIPE_V1; formal source-to-sink menu ordering, "
            "no artificial duration or hidden reservation"
        ),
        fast_search_mode=(
            "FEASIBILITY_FIRST; validated Greedy first, real CP fallback; shared 2400ms deadline"
        ),
    )
    base, base_policy = load_inputs()
    suite = json.loads((ROOT / "benchmarks/scenarios/full_suite.json").read_bytes())
    validate_suite(suite, base, base_policy)
    original = ROOT / "benchmarks/reports/P6-robustness-run2/report.json"
    original_digest = digest(original)
    archived = json.loads(original.read_bytes())
    selected = {r["case_id"] for r in archived["rows"]}
    if case_ids is not None:
        if not case_ids or len(case_ids) != len(set(case_ids)) or not set(case_ids) <= selected:
            raise ValueError("必须选择原固定四十菜单内的非重复案例")
        selected = set(case_ids)
    cases = [c for c in suite["combinations"] if c["case_id"] in selected]
    began = time.perf_counter_ns()
    before = source_hashes()
    report = {
        "status": "RUNNING",
        "scope": "FIXED_INITIAL_PLAN_FEEDBACK_AND_CRITICAL_POLICY_COMPARISON",
        "simulator_version": "observed-start-window-v2-with-continuous-feedback-v2",
        "formal_acceptance": False,
        "partial": len(cases) != 40 or trajectories != 100,
        "started_at": datetime.now(UTC).isoformat(),
        "config": config,
        "suite_hash": suite["suite_hash"],
        "release": base.release.model_dump(mode="json"),
        "base_policy_hash": content_hash(base_policy),
        "source_hashes": before,
        "baseline_report": {
            "path": original.relative_to(ROOT).as_posix(),
            "sha256": original_digest,
        },
        "initial_plans": [],
        "original_initial_artifacts": {},
        "rows": [],
        "replan_computations": [],
        "buffered_enabled_by_default": False,
        "critical_enabled_by_default": False,
        "controls": [
            "Original four controls retain archived initial plans and single-trigger rules",
            "Continuous groups share initial plans, seed, distribution and 2400ms budget",
            "Critical policy receives a newly validated initial plan; ordering change is explicit",
            "Remaining-time updates are synthetic estimates; no private duration or measurement",
            "All failures stay in denominators; each completion gets an independent terminal scan",
        ],
    }
    write_json(output / "report.json", report)
    with SolverWorker() as worker:
        for case in cases:
            case_id = case["case_id"]
            ids = set(case["recipe_ids"])
            knowledge = base.model_copy(
                update={
                    "recipes": tuple(r for r in base.recipes if r.recipe_id.root in ids),
                    "recipe_contexts": tuple(
                        c for c in base.recipe_contexts if c.recipe_id.root in ids
                    ),
                }
            )
            for variant in ("NOMINAL", "BUFFERED", "NOMINAL_FAST", "CRITICAL"):
                artifact = f"initial-{case_id}-{variant}.json.gz"
                if variant == "NOMINAL_FAST":
                    session, problem, initial, initial_row = fast_initial(
                        knowledge,
                        base_policy,
                        case,
                        original,
                        datetime.fromisoformat(suite["time_origin"]),
                    )
                    freeze(
                        output / artifact,
                        {
                            "session": session.model_dump(mode="json") if session else None,
                            "problem": problem.model_dump(mode="json") if problem else None,
                            "result": initial.model_dump(mode="json"),
                            "measurement": initial_row,
                        },
                    )
                elif variant != "CRITICAL":
                    path = original.parent / artifact
                    report["original_initial_artifacts"][artifact] = digest(path)
                    frozen = json.loads(gzip.decompress(path.read_bytes()))
                    initial = PlanningResult.model_validate(frozen["result"])
                    session = (
                        RuntimeSession.model_validate(frozen["session"])
                        if frozen["session"]
                        else None
                    )
                    problem = (
                        SchedulingProblem.model_validate(frozen["problem"])
                        if frozen["problem"]
                        else None
                    )
                    if session is not None:
                        if problem is None or initial.candidate is None:
                            raise ValueError("归档会话缺少完整初排问题或候选")
                        proof = ScheduleValidator().validate(
                            knowledge, problem.runtime, problem, initial.candidate
                        )
                        if not proof.valid:
                            raise ValueError("归档初排不满足当前独立校验：" + artifact)
                    shutil.copyfile(path, output / artifact)
                    initial_row = {
                        "case_id": case_id,
                        "policy": variant,
                        "status": initial.status,
                        "source": "ARCHIVED_INITIAL_INDEPENDENTLY_REVALIDATED",
                        "original_sha256": digest(path),
                    }
                else:
                    values = base_policy.model_dump(mode="json")
                    values.update(
                        critical_window_policy_id="SERIAL_RECIPE_V1",
                        replan_search_mode="FEASIBILITY_FIRST",
                        policy_version=base_policy.policy_version
                        + ":feasibility-replan-v1:serial-recipe-v1",
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
                        "case_id": case_id,
                        "policy": variant,
                        "status": initial.status,
                        "source": "CURRENT_NATIVE_INITIAL_PLAN",
                        "elapsed_ms": elapsed,
                        "prewarm_outside_compute_budget": warming,
                        "policy_hash": content_hash(selected_policy),
                    }
                    freeze(
                        output / artifact,
                        {
                            "session": session.model_dump(mode="json") if session else None,
                            "problem": problem.model_dump(mode="json") if problem else None,
                            "result": initial.model_dump(mode="json"),
                            "measurement": initial_row,
                        },
                    )
                report["initial_plans"].append(initial_row)
                if session is not None and (problem is None or initial.candidate is None):
                    raise ValueError("成功初排缺少完整问题或候选")
                methods = {
                    "CRITICAL": ("CONTINUOUS",),
                    "NOMINAL_FAST": ("REPLAN", "CONTINUOUS"),
                }.get(variant, ("SHIFT", "REPLAN"))
                planner = (
                    FeedbackObservedPlanner(worker, initial.candidate, problem, output, session)
                    if session
                    else None
                )
                cache = BoundedObservationCache()
                for trajectory in range(trajectories):
                    for method in methods:
                        group = variant + "_" + method
                        if session is None:
                            row = {
                                "status": "FAILED",
                                "failure": "INITIAL_PLAN_FAILED: " + initial.failure.message,
                                "failure_detail": {"code": "INITIAL_PLAN_FAILED"},
                                "replan_count": 0,
                            }
                        else:
                            implementation = (
                                ContinuousTrajectorySimulator
                                if method == "CONTINUOUS"
                                else TrajectorySimulator
                            )
                            future = FutureDurations(config["seed"], case_id, trajectory, config)
                            row = implementation(
                                knowledge,
                                session,
                                problem,
                                initial.candidate,
                                future,
                                config,
                                planner=planner if method != "SHIFT" else None,
                                cache=cache,
                            ).run()
                        row.update(
                            case_id=case_id,
                            trajectory=trajectory,
                            group=group,
                            initial_artifact=artifact,
                            initial_candidate_hash=content_hash(initial.candidate)
                            if initial.candidate
                            else None,
                        )
                        name = f"trajectory-{case_id}-{group}-{trajectory:03d}.json.gz"
                        freeze(output / name, row)
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
                                    "feedback_triggers",
                                }
                            }
                            | {"artifact": name}
                        )
                    if trajectory % 10 == 9:
                        print(
                            json.dumps(
                                {
                                    "case": case_id,
                                    "variant": variant,
                                    "trajectory": trajectory,
                                    "rows": len(report["rows"]),
                                }
                            ),
                            flush=True,
                        )
                if planner:
                    report["replan_computations"].extend(planner.computations)
                initial_row["observation_transition_cache"] = {
                    "hits": cache.hits,
                    "real_transitions": cache.misses,
                    "independent_terminal_scans_reused": False,
                }
                freeze(
                    output / f"builds-{case_id}-{variant}.json.gz",
                    {"build_reports": [b.model_dump(mode="json") for b in worker.build_reports]},
                )
                worker.build_reports.clear()
                print(
                    json.dumps(
                        {
                            "case": case_id,
                            "variant": variant,
                            "rows": len(report["rows"]),
                            "native_replans": len(report["replan_computations"]),
                        }
                    ),
                    flush=True,
                )
                write_json(output / "report.json", report)
    report["summaries"] = {
        g: summary([r for r in report["rows"] if r["group"] == g]) for g in GROUPS
    }
    report["pair_transitions"] = {}
    by_key = {(r["case_id"], r["trajectory"], r["group"]): r for r in report["rows"]}
    for control, treatment in (
        ("NOMINAL_REPLAN", "NOMINAL_FAST_REPLAN"),
        ("NOMINAL_FAST_REPLAN", "NOMINAL_FAST_CONTINUOUS"),
        ("NOMINAL_FAST_CONTINUOUS", "CRITICAL_CONTINUOUS"),
    ):
        report["pair_transitions"][control + "->" + treatment] = dict(
            Counter(
                by_key[(c["case_id"], t, control)]["status"]
                + "->"
                + by_key[(c["case_id"], t, treatment)]["status"]
                for c in cases
                for t in range(trajectories)
            )
        )
    report["expected_trajectories"] = len(cases) * trajectories * len(GROUPS)
    report["actual_trajectories"] = len(report["rows"])
    report["source_unchanged"] = before == source_hashes()
    report["original_report_unchanged"] = digest(original) == original_digest
    report["artifact_hashes"] = {p.name: digest(p) for p in sorted(output.glob("*.json.gz"))}
    report["elapsed_ms"] = (time.perf_counter_ns() - began) / 1_000_000
    report["finished_at"] = datetime.now(UTC).isoformat()
    report["status"] = (
        "EXPERIMENT_COMPLETED"
        if report["source_unchanged"]
        and report["original_report_unchanged"]
        and report["actual_trajectories"] == report["expected_trajectories"]
        else "INVALID_EXPERIMENT"
    )
    write_json(output / "report.json", report)
    print(
        json.dumps(
            {"status": report["status"], "summaries": report["summaries"]}, ensure_ascii=False
        ),
        flush=True,
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case-id", action="append")
    parser.add_argument("--trajectories", type=int, default=100)
    args = parser.parse_args()
    report = run(args.output, args.case_id, args.trajectories)
    return 0 if report["status"] == "EXPERIMENT_COMPLETED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
