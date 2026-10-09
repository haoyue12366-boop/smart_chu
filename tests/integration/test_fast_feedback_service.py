"""可选策略经真实 SQLite 事件、独立校验和条件发布，并在重启后保留身份。"""

import pytest

from app.runtime.clock import SimulationClock
from app.runtime.service import RuntimeService
from app.scheduling.cp_sat import CpSatScheduler
from app.services.planning import PlanningService
from app.storage.unit_of_work import UnitOfWork
from tests.runtime_support import ORIGIN, event, policy, synthetic_knowledge


@pytest.mark.parametrize("critical", ["NONE", "SERIAL_RECIPE_V1"])
def test_fast_addition_publishes_and_survives_restart_without_optional_solver(tmp_path, critical):
    class RecordingSolver:
        calls = 0

        def solve(self, *args, **kwargs):
            self.calls += 1
            return CpSatScheduler().solve(*args, **kwargs)

    selected = policy().model_copy(
        update={
            "replan_search_mode": "FEASIBILITY_FIRST",
            "critical_window_policy_id": critical,
            "policy_version": "synthetic-fast-service-v1:" + critical,
        }
    )
    knowledge = synthetic_knowledge()
    store = UnitOfWork(tmp_path / "fast.sqlite")
    store.migrate()
    runtime = RuntimeService(store, knowledge, SimulationClock(ORIGIN))
    session = runtime.create_session("fast", "SIMULATED", ORIGIN, selected)
    solver = RecordingSolver()
    planning = PlanningService(runtime, solver)
    initial = planning.apply_event(
        event(
            session,
            "start",
            "START_SESSION",
            {"recipes": [{"id": "synthetic-0", "name": "合成腌制0"}]},
        )
    )
    assert initial.status == "PUBLISHED", initial
    initial_calls = solver.calls
    assert initial_calls > 0
    addition = event(
        runtime.get("fast"),
        "add",
        "ADD_RECIPE",
        {"recipes": [{"id": "synthetic-1", "name": "合成腌制1"}]},
    )
    result = planning.apply_event(addition)
    assert result.status == "PUBLISHED", result
    assert result.plan.validated.validation.valid
    assert result.budget_ms == 2400 and result.elapsed_ms < 2400
    assert any(t.stage == "FEEDBACK_FEASIBILITY_RETURN" for t in result.planning.timings)
    assert solver.calls == initial_calls + (critical == "NONE")
    assert result.plan.plan_version == 2
    assert result.plan.serial_reference is not None
    assert result.plan.serial_reference.validation.valid
    current = runtime.get("fast")
    assert len(current.menu) == 2 and not current.dispatch_blocked
    assert planning.apply_event(addition).status == "NO_REPLAN"
    restarted = RuntimeService(UnitOfWork(store.path), knowledge, SimulationClock(ORIGIN))
    assert restarted.get("fast") == current
    assert restarted.get("fast").policy == selected
