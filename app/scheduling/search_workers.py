"""按版本化策略分配原生搜索线程；小问题与初排避免并行开销。"""

from app.domain.objectives import ObjectiveStage
from app.domain.scheduling_problem import SchedulingProblem


def search_worker_count(problem: SchedulingProblem, stage: ObjectiveStage | None) -> int:
    policy = problem.policy
    if policy.solver_worker_strategy == "FIXED":
        return policy.max_solver_search_workers
    details = problem.runtime.details
    if (
        details is not None
        and details.planning_kind == "REPLAN"
        and len(problem.recipe_instances) >= 4
        and stage is not None
        and stage.name in {"A_MAKESPAN", "B_SPREAD"}
    ):
        return policy.max_solver_search_workers
    return 1
