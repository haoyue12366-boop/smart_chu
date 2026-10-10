"""云端演示使用有版本的宽预算，不改写原策略或已绑定会话。"""

from typing import Literal

from app.domain.policy import BudgetSpec, SchedulingPolicy


def deployment_policy(
    policy: SchedulingPolicy,
    profile: Literal["STANDARD", "RENDER"],
    *,
    search_workers: int | None = None,
) -> SchedulingPolicy:
    if search_workers is not None and (
        type(search_workers) is not int or not 1 <= search_workers <= 8
    ):
        raise ValueError("求解线程必须是1至8的整数")
    if profile == "STANDARD":
        return _with_search_workers(policy, search_workers)
    budget = BudgetSpec(
        total_ms=90_000,
        greedy_ms=10_000,
        solver_ms=45_000,
        publication_reserve_ms=25_000,
        compilation_ms=20_000,
        quality_ms=10_000,
    )
    return _with_search_workers(
        policy.model_copy(
            update={
                "policy_version": policy.policy_version + ":render-v2",
                "initial_budget": budget,
                "replan_budget": budget,
                "max_solver_search_workers": 1,
            }
        ),
        search_workers,
    )


def _with_search_workers(policy: SchedulingPolicy, workers: int | None) -> SchedulingPolicy:
    if workers is None:
        return policy
    return policy.model_copy(
        update={
            "policy_version": policy.policy_version + f":workers-{workers}",
            "max_solver_search_workers": workers,
        }
    )


def scheduling_strategy_policy(
    policy: SchedulingPolicy, strategy: Literal["FULL_QUALITY", "FT_KITCHEN"]
) -> SchedulingPolicy:
    """仅给新会话绑定FT策略；原知识、设备事实、精度和预算保持。"""
    if strategy == "FULL_QUALITY":
        return policy
    return policy.model_copy(
        update={
            "policy_version": policy.policy_version + ":ft-kitchen-v2",
            "search_strategy": "FT_KITCHEN",
            "solver_worker_strategy": "FT_ADAPTIVE",
            "objective": policy.objective.model_copy(
                update={
                    "stages": ("MAKESPAN", "SPREAD", "HUMAN_BUSY"),
                    "spread_basis": "WORKFLOW_FINISH",
                    "spread_target_sec": 0,
                    "rest_gap_sec": 300,
                    "makespan_extra_basis_points": 0,
                    "makespan_extra_cap_sec": 0,
                }
            ),
        }
    )
