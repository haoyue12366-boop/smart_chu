"""固定真实发布的三菜人工工作对照；禁用共享仅为明确标注的比较策略。"""

import argparse
import time
from pathlib import Path

from app.compiler.compiler import ProblemCompiler
from app.config import AppSettings
from app.domain.base import content_hash
from app.domain.ports import Deadline
from app.domain.scheduling_problem import SchedulingProblem
from app.scheduling.engine import PlanningEngine
from app.scheduling.json_worker import JsonSolverWorker
from app.validation.schedule import ScheduleValidator
from benchmarks.dataset import load_inputs
from benchmarks.performance import write_json
from tests.compiler_support import menu_for, runtime


def run(output: Path) -> dict:
    if output.exists():
        raise FileExistsError("对照证据不能覆盖")
    knowledge, policy = load_inputs(AppSettings())
    identities = (
        "5c8200d96dc6e123a037a202",
        "5fe197175f8f38795ea6fe77",
        "61e6c51fec6e1d65587067e1",
    )
    by_id = {r.recipe_id.root: r for r in knowledge.recipes}
    menu = menu_for(*(by_id[i] for i in identities))
    state = runtime(knowledge)
    rows = []
    with JsonSolverWorker() as worker:
        for shared in (False, True):
            effective = (
                policy
                if shared
                else policy.model_copy(
                    update={"shared_prep": False, "policy_version": "quality-probe-no-shared-v1"}
                )
            )
            started = time.monotonic_ns()
            limit = Deadline(expires_at_ns=started + effective.initial_budget.total_ms * 1_000_000)
            problem = ProblemCompiler().compile(knowledge, menu, state, effective, limit)
            if not isinstance(problem, SchedulingProblem):
                raise RuntimeError(str(problem))
            result = PlanningEngine(validator=ScheduleValidator(), solver=worker).plan(
                problem, knowledge, state, limit
            )
            if result.candidate is None:
                raise RuntimeError(str(result.failure))
            proof = ScheduleValidator().validate(knowledge, state, problem, result.candidate)
            if not proof.valid:
                raise RuntimeError(str(proof.violations))
            rows.append(
                {
                    "shared_prep": shared,
                    "elapsed_ms": (time.monotonic_ns() - started) / 1_000_000,
                    "problem_hash": problem.problem_hash,
                    "shared_candidate_count": len(problem.shared_prep_candidates),
                    "selected_shared_count": sum(
                        a.carrier_id in {c.carrier_id for c in problem.shared_prep_candidates}
                        for a in result.candidate.assignments
                    ),
                    "metrics": result.candidate.metrics.model_dump(mode="json"),
                    "independent_validation": proof.model_dump(mode="json"),
                    "planning_result": result.model_dump(mode="json"),
                    "problem": problem.model_dump(mode="json"),
                }
            )
    report = {
        "scope": "真实 P4 发布；固定合成三菜菜单、未执行初始状态；非 HTTP 性能验收",
        "release": knowledge.release.model_dump(mode="json"),
        "knowledge_hash": content_hash(knowledge),
        "recipes": [{"id": i, "name": by_id[i].name} for i in identities],
        "rows": rows,
    }
    write_json(output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    report = run(parser.parse_args().output)
    for row in report["rows"]:
        print({k: row[k] for k in ("shared_prep", "elapsed_ms", "metrics")}, flush=True)


if __name__ == "__main__":
    main()
