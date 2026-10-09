"""真实构造需要 60 ms 时仍保留合法回退，不能因过度预留下一轮丢掉当前菜单。"""

import time

from app.domain.reports import SolveResult
from app.scheduling.greedy import GreedyScheduler
from tests.integration.test_menu_events_replanning import service, start_event
from tests.runtime_support import event


def test_replan_keeps_real_greedy_fallback_when_construction_takes_sixty_ms(tmp_path, monkeypatch):
    runtime, planning, session = service(tmp_path)
    assert planning.apply_event(start_event(session)).status == "PUBLISHED"

    class SyntheticUnavailableSolver:
        def solve(self, problem, hint, deadline, **kwargs):
            return SolveResult(
                status="UNKNOWN",
                problem_hash=problem.problem_hash,
                diagnostic_message="synthetic:worker unavailable",
            )

    planning.solver = SyntheticUnavailableSolver()
    original = GreedyScheduler.solve

    def delayed_construction(self, problem, deadline):
        started = time.monotonic_ns()
        while time.monotonic_ns() - started < 60_000_000:
            time.sleep(0.001)
        return original(self, problem, deadline)

    monkeypatch.setattr(GreedyScheduler, "solve", delayed_construction)
    current = runtime.get("flow")
    result = planning.apply_event(
        event(
            current,
            "slow-construction-add",
            "ADD_RECIPE",
            {"recipes": [{"id": "synthetic-1", "name": "合成腌制1"}]},
        )
    )
    assert result.status == "PUBLISHED", result.model_dump_json()
    assert result.budget_ms == 2400 and result.attempts == 1
    assert result.planning.selected_candidate_source == "GREEDY"
    assert result.plan.validated.validation.valid
    assert len(result.plan.validated.candidate.recipe_completions) == 2
    assert not runtime.get("flow").dispatch_blocked
