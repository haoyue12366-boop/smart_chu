"""从本地不可变发布执行真实规划，保存秒、小数分钟和独立校验证据。"""

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

from app.compiler.time_projection import decimal_minutes, project_minutes
from app.domain.ids import RecipeId
from app.domain.policy import SchedulingPolicy
from app.domain.ports import Deadline, PlanningRequest
from app.domain.runtime_snapshot import RuntimeSnapshot
from app.domain.scheduling_problem import RecipeInstance
from app.domain.time import TimeOrigin
from app.knowledge.loader import read_release_ref
from app.knowledge.repository import SnapshotKnowledgeRepository
from app.scheduling.worker import SolverWorker
from app.services.planning_core import PlanningCore

ROOT = Path(__file__).resolve().parents[1]


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-id", default="development-v3-rebased-v2-all")
    parser.add_argument("--recipe-id", action="append")
    parser.add_argument("--budget-ms", type=int, default=4200)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "benchmarks/reports/P2-sample-plan.json"
    )
    args = parser.parse_args()
    if args.budget_ms <= 0:
        parser.error("budget-ms 必须为正整数")
    started = time.monotonic_ns()
    root = ROOT / "data/releases"
    release = read_release_ref(root, args.release_id)
    repository = SnapshotKnowledgeRepository(root)
    repository.load(release)
    with repository.acquire(release) as lease:
        ids = (
            tuple(RecipeId(r) for r in args.recipe_id)
            if args.recipe_id
            else tuple(
                r.recipe_id
                for r in lease.loaded.snapshot.knowledge.recipes
                if r.name == "韩式泡菜鸦片鱼头"
            )
        )
        knowledge = lease.select(ids)
    load_ms = (time.monotonic_ns() - started) // 1_000_000
    request = PlanningRequest(
        request_id="sample-plan",
        menu=tuple(
            RecipeInstance(recipe_instance_id=f"instance-{i}", recipe_id=r.recipe_id, name=r.name)
            for i, r in enumerate(knowledge.recipes)
        ),
        policy=SchedulingPolicy(policy_version="p2-base-decimal-v1"),
    )
    runtime = RuntimeSnapshot(
        session_id="sample-plan",
        state_revision=0,
        current_plan_version=0,
        knowledge_version=release.knowledge_version,
        rule_version=release.rule_version,
        snapshot_id=release.snapshot_id,
        time_origin=TimeOrigin(start_at=datetime.now().astimezone().replace(microsecond=0)),
        now_offset_sec=0,
        execution_mode="SIMULATED",
    )
    with SolverWorker() as worker:
        core = PlanningCore(solver=worker)
        result = core.compute(
            request,
            knowledge,
            runtime,
            Deadline(expires_at_ns=time.monotonic_ns() + args.budget_ms * 1_000_000),
        )
        if core.last_problem is not None:
            write_json(
                args.output.with_suffix(".problem.json"), core.last_problem.model_dump(mode="json")
            )
        write_json(
            args.output.with_suffix(".builds.json"),
            [r.model_dump(mode="json") for r in worker.build_reports],
        )
        report = {
            "release": release.model_dump(mode="json"),
            "release_kind": release.release_kind,
            "formal_review_complete": release.release_kind == "competition",
            "cold_knowledge_load_ms": load_ms,
            "cold_worker_startup_ms": worker.startup_ms,
            "request": request.model_dump(mode="json"),
            "result": result.model_dump(mode="json"),
            "minute_projection": [
                {
                    "carrier_id": a.carrier_id.root,
                    "interval_minute": project_minutes(a.interval),
                    "duration_minute": decimal_minutes(a.interval.end_sec - a.interval.start_sec),
                }
                for a in result.candidate.assignments
            ]
            if result.candidate is not None
            else [],
        }
        write_json(args.output, report)
    print(json.dumps({"status": result.status, "output": str(args.output)}, ensure_ascii=False))
    return 0 if result.status == "VALIDATED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
