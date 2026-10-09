"""可选反馈快速模式只提前返回完整已验证候选，失败继续实际 CP 路径。"""

import time

from app.compiler.compiler import ProblemCompiler
from app.domain.policy import SchedulingPolicy
from app.domain.ports import Deadline
from app.domain.reports import GreedyResult
from app.scheduling.cp_sat import CpSatScheduler
from app.scheduling.engine import PlanningEngine
from app.scheduling.greedy import GreedyScheduler
from app.validation.schedule import ScheduleValidator
from tests.integration.test_robustness_dispatch import witness


def current_problem():
    knowledge, session, _, _ = witness()
    selected = SchedulingPolicy.model_validate(
        {**session.policy.model_dump(mode="json"), "replan_search_mode": "FEASIBILITY_FIRST"}
    )
    state = session.runtime.model_copy(
        update={"details": session.runtime.details.model_copy(update={"planning_kind": "REPLAN"})}
    )
    deadline = Deadline(expires_at_ns=time.monotonic_ns() + 2_400_000_000)
    problem = ProblemCompiler().compile(knowledge, session.menu, state, selected, deadline)
    return knowledge, state, problem, deadline


def test_valid_greedy_feedback_candidate_does_not_start_optional_optimization():
    class ForbiddenSolver:
        def solve(self, *args, **kwargs):
            raise AssertionError("已经合法的快速反馈不应启动可选优化")

    knowledge, state, problem, deadline = current_problem()
    result = PlanningEngine(validator=ScheduleValidator(), solver=ForbiddenSolver()).plan(
        problem, knowledge, state, deadline
    )
    assert result.status == "VALIDATED" and result.validation.valid
    assert result.selected_candidate_source == "GREEDY"
    assert any(t.stage == "FEEDBACK_FEASIBILITY_RETURN" for t in result.timings)
    assert not result.stage_results
    assert not result.human_objective_optimized and not result.stability_objective_optimized
    assert time.monotonic_ns() < deadline.expires_at_ns


def test_failed_fast_construction_still_uses_real_cp_sat(monkeypatch):
    knowledge, state, problem, deadline = current_problem()
    monkeypatch.setattr(
        GreedyScheduler,
        "solve",
        lambda *args, **kwargs: GreedyResult(
            status="CONSTRUCTION_FAILED", reason="explicit failed reference"
        ),
    )
    result = PlanningEngine(validator=ScheduleValidator(), solver=CpSatScheduler()).plan(
        problem, knowledge, state, deadline
    )
    assert result.status == "VALIDATED", result.failure
    assert result.stage_results
    assert any(r.objective_stage == "A_MAKESPAN" for r in result.stage_results)
    assert result.selected_candidate_source == "CP_SAT"
    assert result.validation.valid


def test_fast_mode_keeps_default_policy_json_unchanged():
    _, session, _, _ = witness()
    assert "replan_search_mode" not in session.policy.model_dump(mode="json")
