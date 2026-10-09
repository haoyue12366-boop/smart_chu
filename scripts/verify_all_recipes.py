"""固定发布的全量单菜真实主链验收；开发数据不升级为人工批准。"""

import argparse
import hashlib
import math
import platform
import time
from datetime import datetime
from pathlib import Path

from app.domain.canonical_recipe import CanonicalRecipeModel
from app.domain.policy import SchedulingPolicy
from app.domain.ports import Deadline, PlanningRequest
from app.domain.runtime_snapshot import RuntimeSnapshot
from app.domain.scheduling_problem import RecipeInstance
from app.domain.time import TimeOrigin
from app.knowledge.loader import read_release_ref
from app.knowledge.repository import SnapshotKnowledgeRepository
from app.scheduling.worker import SolverWorker
from app.services.planning_core import PlanningCore
from scripts.run_sample_plan import ROOT, write_json


def review_pending_count(recipes: tuple[CanonicalRecipeModel, ...]) -> int:
    # 菜谱批准绑定整个路径内容哈希；模型建议的原始状态不必被批量改写。
    return sum(r.review_status != "APPROVED" or r.approval is None for r in recipes)


def verify_all(root: Path, release_id: str, output: Path) -> dict:
    cold = time.monotonic_ns()
    release = read_release_ref(root, release_id)
    repository = SnapshotKnowledgeRepository(root)
    repository.load(release)
    with repository.acquire(release) as lease:
        source = lease.loaded.snapshot.knowledge
        knowledge = lease.select(tuple(r.recipe_id for r in source.recipes))
    load_ms = (time.monotonic_ns() - cold) // 1_000_000
    policy = SchedulingPolicy(policy_version="p2-base-decimal-v1")
    runtime = RuntimeSnapshot(
        session_id="all-recipes-development",
        state_revision=0,
        current_plan_version=0,
        knowledge_version=release.knowledge_version,
        rule_version=release.rule_version,
        snapshot_id=release.snapshot_id,
        now_offset_sec=0,
        execution_mode="SIMULATED",
        time_origin=TimeOrigin(start_at=datetime.fromisoformat("2026-09-28T23:59:00+08:00")),
    )
    results = []
    recoveries = []
    with SolverWorker() as worker:
        startup_ms = worker.startup_ms
        core = PlanningCore(solver=worker)
        for index, recipe in enumerate(knowledge.recipes):
            previous_pid = worker.process_id
            recovery_start = time.monotonic_ns()
            if not worker.warmup():
                raise RuntimeError("下一次请求前工作进程恢复失败，验收终止")
            if worker.process_id != previous_pid:
                recoveries.append(
                    {
                        "before_recipe_id": recipe.recipe_id.root,
                        "elapsed_ms": (time.monotonic_ns() - recovery_start) / 1_000_000,
                    }
                )
            started = time.monotonic_ns()
            request = PlanningRequest(
                request_id="single:" + recipe.recipe_id.root,
                policy=policy,
                menu=(
                    RecipeInstance(
                        recipe_instance_id="instance-0",
                        recipe_id=recipe.recipe_id,
                        name=recipe.name,
                    ),
                ),
            )
            build_start = len(worker.build_reports)
            result = core.compute(
                request,
                knowledge,
                runtime,
                Deadline(expires_at_ns=started + policy.initial_budget.total_ms * 1_000_000),
            )
            elapsed_ms = (time.monotonic_ns() - started) / 1_000_000
            path = output / "plans" / (recipe.recipe_id.root + ".json")
            write_json(
                path,
                {
                    "release": release.model_dump(mode="json"),
                    "request": request.model_dump(mode="json"),
                    "result": result.model_dump(mode="json"),
                    "problem": core.last_problem.model_dump(mode="json")
                    if core.last_problem
                    else None,
                    "build_reports": [
                        r.model_dump(mode="json") for r in worker.build_reports[build_start:]
                    ],
                    "elapsed_ms": elapsed_ms,
                },
            )
            results.append(
                {
                    "recipe_id": recipe.recipe_id.root,
                    "name": recipe.name,
                    "status": result.status,
                    "failure": result.failure.model_dump(mode="json") if result.failure else None,
                    "elapsed_ms": elapsed_ms,
                    "plan_artifact": str(path.relative_to(output)),
                    "artifact_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "problem_hash": core.last_problem.problem_hash if core.last_problem else None,
                    "candidate_hash": result.candidate.candidate_hash if result.candidate else None,
                    "solver_candidate_count": sum(
                        r.candidate is not None for r in result.stage_results
                    ),
                    "rejected_candidate_count": len(result.rejected_candidates),
                }
            )
            print(f"{index + 1}/100 {recipe.name}: {result.status} {elapsed_ms:.1f} ms", flush=True)
    times = sorted(row["elapsed_ms"] for row in results)
    pending = review_pending_count(source.recipes)
    successes = sum(row["status"] == "VALIDATED" for row in results)
    report = {
        "release": release.model_dump(mode="json"),
        "snapshot_hash": knowledge.snapshot_hash,
        "canonical_hash": source.content_hash,
        "recipe_count": len(results),
        "success_count": successes,
        "review_pending_count": pending,
        "formal_release_eligible": pending == 0
        and len(results) == successes == 100
        and source.validation.formal_release_eligible,
        "execution_mode": "SIMULATED",
        "policy": policy.model_dump(mode="json"),
        "cold_knowledge_load_ms": load_ms,
        "cold_worker_startup_ms": startup_ms,
        "between_request_recoveries": recoveries,
        "environment": {
            "platform": platform.platform(),
            "processor": platform.processor(),
            "python": platform.python_version(),
            "concurrent_jobs": 1,
        },
        "performance": {
            "p50_ms": times[math.ceil(len(times) * 0.5) - 1],
            "p95_ms": times[math.ceil(len(times) * 0.95) - 1],
            "max_ms": max(times),
            "over_budget_count": sum(t > policy.initial_budget.total_ms for t in times),
            "failure_count": len(results) - successes,
            "greedy_only_count": sum(
                r["status"] == "VALIDATED" and not r["solver_candidate_count"] for r in results
            ),
        },
        "results": results,
    }
    write_json(output / "report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-id", default="development-v3-rebased-v2-all")
    parser.add_argument(
        "--output", type=Path, default=ROOT / "benchmarks/reports/P1-P2-all-recipes"
    )
    args = parser.parse_args()
    report = verify_all(ROOT / "data/releases", args.release_id, args.output)
    return 0 if report["recipe_count"] == report["success_count"] == 100 else 1


if __name__ == "__main__":
    raise SystemExit(main())
