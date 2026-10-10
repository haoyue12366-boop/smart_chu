"""线程选择只改变搜索资源，旧策略 JSON 和问题事实保留。"""

import pytest
from pydantic import ValidationError

from app.config import AppSettings
from app.domain.base import content_hash
from app.domain.objectives import ObjectiveStage
from app.domain.policy import SchedulingPolicy
from app.services.deployment_policy import deployment_policy, scheduling_strategy_policy
from tests.unit.test_ft_kitchen_strategy import ft_case


@pytest.mark.parametrize("workers", [1, 2, 4, 8])
def test_explicit_thread_configuration_is_validated_and_versioned(workers, monkeypatch):
    monkeypatch.setenv("SMART_COOKING_SOLVER_SEARCH_WORKERS", str(workers))
    assert AppSettings.from_environment().solver_search_workers == workers
    original = SchedulingPolicy(policy_version="baseline")
    cloud = deployment_policy(original, "RENDER", search_workers=workers)
    assert cloud.max_solver_search_workers == workers
    assert cloud.policy_version.endswith(f":workers-{workers}")
    assert original.max_solver_search_workers == 4
    assert cloud.initial_budget.solver_ms == 45_000
    assert SchedulingPolicy.model_validate_json(cloud.model_dump_json()) == cloud


@pytest.mark.parametrize("value", ["0", "9", "-1", "nan", "1.5", "invalid"])
def test_invalid_thread_configuration_fails(value, monkeypatch):
    monkeypatch.setenv("SMART_COOKING_SOLVER_SEARCH_WORKERS", value)
    with pytest.raises(ValueError):
        AppSettings.from_environment()


def test_legacy_policy_keeps_json_hash_and_fixed_threads():
    from app.scheduling.search_workers import search_worker_count

    original = SchedulingPolicy(policy_version="legacy", max_solver_search_workers=4)
    assert "solver_worker_strategy" not in original.model_dump(mode="json")
    restored = SchedulingPolicy.model_validate_json(original.model_dump_json())
    assert content_hash(restored) == content_hash(original)
    _, problem, _ = ft_case()
    problem = problem.model_copy(update={"policy": original})
    assert search_worker_count(problem, ObjectiveStage(name="A_MAKESPAN")) == 4
    assert search_worker_count(problem, None) == 4


@pytest.mark.parametrize(
    ("kind", "count", "stage_name", "expected"),
    [
        ("INITIAL", 5, "A_MAKESPAN", 1),
        ("INITIAL", 5, "B_SPREAD", 1),
        ("REPLAN", 3, "A_MAKESPAN", 1),
        ("REPLAN", 4, "A_MAKESPAN", 4),
        ("REPLAN", 5, "B_SPREAD", 4),
        ("REPLAN", 5, "D_HUMAN", 1),
        ("REPLAN", 5, None, 1),
    ],
)
def test_ft_adaptive_threads_use_one_for_small_initial_and_optional_human_search(
    kind, count, stage_name, expected
):
    from app.scheduling.search_workers import search_worker_count

    _, problem, _ = ft_case()
    policy = scheduling_strategy_policy(
        deployment_policy(problem.policy, "RENDER", search_workers=4), "FT_KITCHEN"
    )
    details = problem.runtime.details.model_copy(update={"planning_kind": kind})
    # 仅合成线程选择输入形状，不将重复菜实例用作合法排程证据。
    problem = problem.model_copy(
        update={
            "policy": policy,
            "recipe_instances": (problem.recipe_instances[0],) * count,
            "runtime": problem.runtime.model_copy(update={"details": details}),
        }
    )
    before = problem.model_dump_json()
    stage = ObjectiveStage(name=stage_name) if stage_name else None
    assert search_worker_count(problem, stage) == expected
    assert problem.model_dump_json() == before
    limited = problem.model_copy(
        update={"policy": policy.model_copy(update={"max_solver_search_workers": 1})}
    )
    assert search_worker_count(limited, stage) == 1


@pytest.mark.parametrize("workers", [True, 0, 9])
def test_policy_rejects_invalid_native_thread_count(workers):
    with pytest.raises(ValidationError):
        SchedulingPolicy(policy_version="bad", max_solver_search_workers=workers)
