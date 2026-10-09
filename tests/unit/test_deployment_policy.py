"""部署预算有独立版本；比赛策略、工艺和既有会话保持可追溯。"""

import time

import pytest

from app.config import AppSettings
from app.domain.policy import ObjectiveSpec, SchedulingPolicy
from app.domain.reports import GreedyResult, SolveResult
from app.scheduling.engine import PlanningEngine
from app.scheduling.greedy import GreedyScheduler
from app.services.deployment_policy import deployment_policy
from app.validation.schedule import ScheduleValidator
from tests.unit.test_cp_sat_model import deadline
from tests.unit.test_schedule_validator import example


def test_render_is_detected_without_dashboard_changes(monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.delenv("SMART_COOKING_PLANNING_PROFILE", raising=False)
    assert AppSettings.from_environment().planning_profile == "RENDER"


def test_explicit_standard_profile_overrides_render_detection(monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setenv("SMART_COOKING_PLANNING_PROFILE", "STANDARD")
    assert AppSettings.from_environment().planning_profile == "STANDARD"


def test_standard_budget_is_unchanged():
    original = SchedulingPolicy(policy_version="original")
    assert deployment_policy(original, "STANDARD") is original


def test_render_policy_preserves_rules_and_separately_bounds_compilation():
    original = SchedulingPolicy.model_validate_json(AppSettings().policy_path.read_bytes())
    before = original.model_dump_json()
    cloud = deployment_policy(original, "RENDER")
    assert cloud.policy_version == original.policy_version + ":render-v2"
    for budget in (cloud.initial_budget, cloud.replan_budget):
        assert budget.total_ms == 90_000
        assert budget.greedy_ms == 10_000
        assert budget.solver_ms == 45_000
        assert budget.publication_reserve_ms == 25_000
        assert budget.compilation_limit_ms == 20_000
        assert budget.quality_ms == 10_000
    assert cloud.max_solver_search_workers == 1
    assert (
        cloud.model_copy(
            update={
                "policy_version": original.policy_version,
                "initial_budget": original.initial_budget,
                "replan_budget": original.replan_budget,
                "max_solver_search_workers": original.max_solver_search_workers,
            }
        )
        == original
    )
    assert original.model_dump_json() == before
    assert "compilation_ms" not in original.initial_budget.model_dump(mode="json")


@pytest.mark.parametrize("value", ["", "false", "FALSE"])
def test_other_hosts_keep_standard_profile(value, monkeypatch):
    monkeypatch.setenv("RENDER", value)
    monkeypatch.delenv("SMART_COOKING_PLANNING_PROFILE", raising=False)
    assert AppSettings.from_environment().planning_profile == "STANDARD"


def test_serial_reference_phase_scales_with_authorized_cloud_budget(monkeypatch):
    knowledge, state, problem, _ = example()
    policy = deployment_policy(
        problem.policy.model_copy(
            update={"objective": ObjectiveSpec(stages=("SPREAD", "HUMAN_BUSY", "MAKESPAN"))}
        ),
        "RENDER",
    )
    problem = problem.model_copy(update={"policy": policy})

    def empty(*args):
        return GreedyResult(status="CONSTRUCTION_FAILED")

    monkeypatch.setattr(GreedyScheduler, "solve", empty)
    monkeypatch.setattr(GreedyScheduler, "solve_serial", empty)
    calls = []

    class UnavailableSolver:
        def solve(self, problem, hint, limit, **kwargs):
            calls.append((kwargs, limit.expires_at_ns - time.monotonic_ns()))
            return SolveResult(status="UNKNOWN", problem_hash=problem.problem_hash)

    result = PlanningEngine(validator=ScheduleValidator(), solver=UnavailableSolver()).plan(
        problem, knowledge, state, deadline(90)
    )
    assert result.status == "FAILED" and result.candidate is None
    assert calls[0][0]["serial_menu"]
    assert 10_000_000_000 < calls[0][1] <= 11_250_000_000


def test_quality_retries_share_one_optional_search_limit():
    knowledge, state, problem, _ = example()
    cloud = deployment_policy(
        problem.policy.model_copy(
            update={"objective": ObjectiveSpec(stages=("SPREAD", "HUMAN_BUSY", "MAKESPAN"))}
        ),
        "RENDER",
    )
    budget = cloud.initial_budget.model_copy(update={"quality_ms": 200})
    problem = problem.model_copy(
        update={"policy": cloud.model_copy(update={"initial_budget": budget})}
    )
    limits = []

    class UnavailableSolver:
        def solve(self, problem, hint, limit, **kwargs):
            if kwargs.get("stage") and kwargs["stage"].name == "E_QUALITY":
                limits.append(limit.expires_at_ns)
                assert 0 < limit.expires_at_ns - time.monotonic_ns() <= 200_000_000
                time.sleep(0.03)
            return SolveResult(status="UNKNOWN", problem_hash=problem.problem_hash)

    result = PlanningEngine(validator=ScheduleValidator(), solver=UnavailableSolver()).plan(
        problem, knowledge, state, deadline(90)
    )
    assert result.status == "VALIDATED" and result.validation.valid
    assert len(limits) == 2 and limits[0] == limits[1]
    assert not result.human_objective_optimized
