"""真实授权发布菜谱；模拟事件明确标注，覆盖实际物料与冷藏等待。"""

from app.domain.candidates import stable_id
from app.runtime.clock import SimulationClock
from app.runtime.service import RuntimeService
from app.runtime.simulator import Simulator
from app.scheduling.cp_sat import CpSatScheduler
from app.services.planning import PlanningService
from app.storage.unit_of_work import UnitOfWork
from tests.runtime_support import ORIGIN, event, p4_knowledge, policy


def test_real_marinade_chain_has_material_evidence_and_no_human_during_wait(tmp_path):
    knowledge = p4_knowledge()
    recipe = next(r for r in knowledge.recipes if r.recipe_id.root == "65795250c458a177d8438dea")
    store = UnitOfWork(tmp_path / "real.sqlite")
    store.migrate()
    runtime = RuntimeService(store, knowledge, SimulationClock(ORIGIN))
    session = runtime.create_session("real", "SIMULATED", ORIGIN, policy())
    planning = PlanningService(runtime, CpSatScheduler())
    outcome = planning.apply_event(
        event(
            session,
            "real-start",
            "START_SESSION",
            {"recipes": [{"id": recipe.recipe_id, "name": recipe.name}]},
        )
    )
    assert outcome.status == "PUBLISHED", outcome
    current = runtime.get("real")
    task = stable_id("task", current.menu[0].recipe_instance_id.root, "op_009_01")
    binding = next(b for b in current.bindings if task in {t.root for t in b.assignment.task_ids})
    simulator = Simulator(runtime, "real", seed=31)
    emitted = simulator.advance(binding.assignment.interval.start_sec)
    assert emitted and all(e.source == "SIMULATED" for e in emitted)
    current = runtime.get("real")
    running = next(e for e in current.runtime.executions if task in {t.root for t in e.task_ids})
    assert running.status == "RUNNING"
    occupied = [
        o
        for o in current.runtime.details.occupancies
        if o.execution_id == running.execution_id and o.released_at is None
    ]
    assert occupied and all(o.resource.resource_type != "HUMAN" for o in occupied)
    assert current.ledger
    assert any(entry.kind == "CONSUME" for entry in current.ledger)
    assert any(entry.kind == "PRODUCE" for entry in current.ledger)
    simulator.advance(outcome.plan.validated.candidate.metrics.makespan_sec)
    finished = runtime.get("real")
    assert all(e.status == "COMPLETED" for e in finished.runtime.executions)
    assert len({t for e in finished.runtime.executions for t in e.completed_task_ids}) == len(
        recipe.operations
    )
    assert not finished.dispatch_blocked
