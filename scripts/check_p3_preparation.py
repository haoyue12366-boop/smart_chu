"""离线检查P3准备包；全量编译和两菜单独基线不等于共享求解验收。"""

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from app.compiler.compiler import ProblemCompiler
from app.compiler.instantiate import instantiate
from app.domain.candidates import InstantiationResult
from app.domain.compatibility import GroupContext, GroupRuleSpec
from app.domain.policy import SchedulingPolicy
from app.domain.ports import Deadline
from app.domain.reports import CompilationFailure
from app.domain.runtime_snapshot import RuntimeSnapshot
from app.domain.scheduling_problem import RecipeInstance
from app.domain.time import TimeOrigin
from app.knowledge.loader import read_release_ref
from app.knowledge.repository import SnapshotKnowledgeRepository
from app.knowledge.rules import RuleEngine
from app.pipeline.p3_preparation import VERSION
from app.scheduling.greedy import GreedyScheduler
from app.validation.schedule import ScheduleValidator


def check_preparation(preparation: Path, output: Path) -> dict[str, object]:
    started = time.perf_counter()
    release_root = preparation / "releases"
    ref = read_release_ref(release_root, VERSION + "-all")
    repository = SnapshotKnowledgeRepository(release_root)
    repository.load(ref)
    with repository.acquire(ref) as lease:
        all_ids = tuple(r.recipe_id for r in lease.loaded.snapshot.knowledge.recipes)
        knowledge = lease.select(all_ids)
    runtime = RuntimeSnapshot(
        session_id="p3-preparation-offline",
        state_revision=0,
        current_plan_version=0,
        knowledge_version=ref.knowledge_version,
        rule_version=ref.rule_version,
        snapshot_id=ref.snapshot_id,
        time_origin=TimeOrigin(start_at=datetime.now(UTC)),
        now_offset_sec=0,
        execution_mode="SIMULATED",
    )
    policy = SchedulingPolicy(policy_version="p3-preparation-standalone-baseline-v1")
    compiler = ProblemCompiler()
    compile_results = []
    for i, recipe in enumerate(knowledge.recipes):
        view = repository.select((recipe.recipe_id,))
        menu = (
            RecipeInstance(
                recipe_instance_id="prepare-single", recipe_id=recipe.recipe_id, name=recipe.name
            ),
        )
        problem = compiler.compile(
            view,
            menu,
            runtime,
            policy,
            Deadline(expires_at_ns=time.monotonic_ns() + 10_000_000_000),
        )
        if isinstance(problem, CompilationFailure):
            compile_results.append(
                {"recipe_id": recipe.recipe_id.root, "status": "FAILED", "reason": problem.message}
            )
        else:
            compile_results.append(
                {
                    "recipe_id": recipe.recipe_id.root,
                    "status": "COMPILED",
                    "problem_hash": problem.problem_hash,
                }
            )
        if (i + 1) % 25 == 0:
            print(f"P3准备包已完成 {i + 1}/{len(knowledge.recipes)} 道离线编译检查", flush=True)
    group_results = []
    wanted = tuple(
        dict.fromkeys(
            b.recipe_id
            for rule in knowledge.rules
            for b in GroupRuleSpec.model_validate_json(rule.group_compatibility_predicate).bindings
        )
    )
    view = repository.select(wanted)
    menu = tuple(
        RecipeInstance(recipe_instance_id=f"prepare-group-{i}", recipe_id=r.recipe_id, name=r.name)
        for i, r in enumerate(view.recipes)
    )
    instances = instantiate(menu, view, runtime)
    if not isinstance(instances, InstantiationResult):
        raise ValueError(instances.message)
    instance_recipe = {m.recipe_instance_id: m.recipe_id for m in menu}
    context = GroupContext(
        knowledge=view, runtime=runtime, menu=menu, allow_delegated_estimates=True
    )
    for rule in view.rules:
        spec = GroupRuleSpec.model_validate_json(rule.group_compatibility_predicate)
        keys = {(b.recipe_id, b.operation_id) for b in spec.bindings}
        members = tuple(
            t
            for t in instances.tasks
            if (instance_recipe[t.recipe_instance_id], t.operation_id) in keys
        )
        decision = RuleEngine().evaluate_group(members, context)
        group_results.append({"rule_id": rule.rule_id, **decision.model_dump(mode="json")})
    problem = compiler.compile(
        view, menu, runtime, policy, Deadline(expires_at_ns=time.monotonic_ns() + 10_000_000_000)
    )
    if isinstance(problem, CompilationFailure):
        raise ValueError(problem.message)
    result = GreedyScheduler().solve(
        problem, Deadline(expires_at_ns=time.monotonic_ns() + 10_000_000_000)
    )
    if result.candidate is None:
        raise ValueError("两菜单独基线没有完整候选：" + str(result.reason))
    validation = ScheduleValidator().validate(view, runtime, problem, result.candidate)
    output.mkdir(parents=True, exist_ok=True)
    (output / "baseline.problem.json").write_text(
        problem.model_dump_json(indent=2), encoding="utf-8"
    )
    (output / "baseline.plan.json").write_text(
        result.candidate.model_dump_json(indent=2), encoding="utf-8"
    )
    (output / "baseline.validation.json").write_text(
        validation.model_dump_json(indent=2), encoding="utf-8"
    )
    report = {
        "kind": "P3_PREPARATION_OFFLINE_CHECK",
        "release": ref.model_dump(mode="json"),
        "source_recipe_count": len(all_ids),
        "compiled_recipe_count": sum(r["status"] == "COMPILED" for r in compile_results),
        "compilation_results": compile_results,
        "compatible_development_groups": sum(bool(r["compatible"]) for r in group_results),
        "group_results": group_results,
        "baseline_plan_validated": validation.valid,
        "baseline_recipe_count": len(menu),
        "shared_planning_enabled": False,
        "formal_release_eligible": False,
        "timing_scope": "离线准备检查；每次编译/基线求解最多10秒，不是4200ms服务性能验收",
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preparation", type=Path, default=root / "data/preparations/p3-v1")
    parser.add_argument("--output", type=Path, default=root / "benchmarks/reports/P3-preparation")
    args = parser.parse_args()
    report = check_preparation(args.preparation, args.output)
    print(
        json.dumps(
            {
                k: report[k]
                for k in (
                    "compiled_recipe_count",
                    "compatible_development_groups",
                    "baseline_plan_validated",
                    "elapsed_sec",
                )
            },
            ensure_ascii=False,
        )
    )
    return (
        0
        if (
            report["compiled_recipe_count"] == 100
            and report["compatible_development_groups"] == 2
            and report["baseline_plan_validated"]
        )
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
