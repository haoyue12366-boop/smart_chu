"""云端演示使用有版本的宽预算，不改写原策略或已绑定会话。"""

from typing import Literal

from app.domain.policy import BudgetSpec, SchedulingPolicy


def deployment_policy(
    policy: SchedulingPolicy, profile: Literal["STANDARD", "RENDER"]
) -> SchedulingPolicy:
    if profile == "STANDARD":
        return policy
    budget = BudgetSpec(
        total_ms=90_000,
        greedy_ms=10_000,
        solver_ms=45_000,
        publication_reserve_ms=25_000,
        compilation_ms=20_000,
    )
    return policy.model_copy(
        update={
            "policy_version": policy.policy_version + ":render-v1",
            "initial_budget": budget,
            "replan_budget": budget,
            "max_solver_search_workers": 1,
        }
    )
