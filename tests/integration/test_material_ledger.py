"""明确合成的 10g 两阶段菜谱，使用真实规划、SQLite 与物料事件。"""

import pytest

from app.domain.runtime_facts import RationalAmount
from app.runtime.clock import SimulationClock
from app.runtime.notifications import NotificationService
from app.runtime.service import RuntimeService
from app.runtime.simulated_materials import simulated_payload
from app.runtime.simulator import Simulator
from app.scheduling.cp_sat import CpSatScheduler
from app.services.planning import PlanningService
from app.storage.repositories import RuntimeRepository
from app.storage.unit_of_work import UnitOfWork
from tests.runtime_support import ORIGIN, event, p4_knowledge, policy
from tests.unit.test_compiler_material_stock import source


def material_service(tmp_path):
    synthetic, _, _, _ = source()
    knowledge = p4_knowledge().model_copy(
        update={"recipes": synthetic.recipes, "recipe_contexts": (), "rules": ()}
    )
    recipe = knowledge.recipes[0]
    store = UnitOfWork(tmp_path / "material.sqlite")
    store.migrate()
    runtime = RuntimeService(store, knowledge, SimulationClock(ORIGIN))
    session = runtime.create_session("material", "SIMULATED", ORIGIN, policy())
    planning = PlanningService(runtime, CpSatScheduler())
    started = event(
        session,
        "start",
        "START_SESSION",
        {"recipes": [{"id": recipe.recipe_id, "name": recipe.name}]},
    )
    outcome = planning.apply_event(started)
    assert outcome.status == "PUBLISHED", outcome
    return runtime, planning


def test_publication_reserves_actual_lot_and_future_output_without_creating_stock(tmp_path):
    runtime, _ = material_service(tmp_path)
    current = runtime.get("material")
    assert len(current.runtime.details.lots) == 1
    raw = current.runtime.details.lots[0]
    assert raw.available.fraction() == raw.reserved.fraction() == 10
    assert len(current.allocations) == 1
    assert len(current.future_allocations) == 2
    assert {a.status for a in current.future_allocations} == {"PLANNED", "FULFILLED"}
    assert any(e.kind == "RESERVE" for e in current.ledger)


def test_replan_preserves_consumption_and_repeated_cumulative_report(tmp_path):
    runtime, planning = material_service(tmp_path)
    simulator = Simulator(runtime, "material")
    simulator.advance(0)
    before = runtime.get("material")
    raw = before.runtime.details.lots[0]
    assert raw.available.fraction() == raw.reserved.fraction() == 0
    assert before.allocations[0].status == "CONSUMED"
    changed = planning.apply_event(
        event(
            before,
            "delay",
            "DELAY_RECIPE",
            {"recipe_instance_id": before.menu[0].recipe_instance_id, "earliest_start_sec": 120},
        )
    )
    assert changed.status == "PUBLISHED", changed
    current = runtime.get("material")
    assert current.allocations[0] == before.allocations[0]
    assert current.runtime.details.lots[0].available.fraction() == 0
    simulator.advance(60)
    produced = runtime.get("material")
    cut = next(lot for lot in produced.runtime.details.lots if lot.produced_by_execution_id)
    assert cut.available.fraction() == cut.reserved.fraction() == 10
    raw_entries = [e for e in produced.ledger if e.lot_id == raw.lot_id and e.kind == "CONSUME"]
    assert len(raw_entries) == 1
    assert any(a.status == "CANCELLED" for a in produced.future_allocations if a.plan_version == 1)
    simulator.advance(changed.plan.validated.candidate.metrics.makespan_sec)
    assert all(e.status == "COMPLETED" for e in runtime.get("material").runtime.executions)


def test_shortage_releases_reservations_and_preserves_adjustment_when_replan_fails(tmp_path):
    runtime, planning = material_service(tmp_path)
    current = runtime.get("material")
    raw = current.runtime.details.lots[0]
    shortage = event(
        current,
        "shortage",
        "MATERIAL_SHORTAGE",
        {
            "lot_id": raw.lot_id,
            "before": {"value": 10, "unit": "g", "scale": 1},
            "after": {"value": 9, "unit": "g", "scale": 1},
            "reason": "synthetic shortage",
            "evidence_refs": ["synthetic:scale"],
        },
    )
    result = planning.apply_event(shortage)
    assert result.status == "FAILED", result
    after = runtime.get("material")
    assert after.runtime.details.lots[0].available.fraction() == 9
    assert after.runtime.details.lots[0].reserved.fraction() == 0
    assert after.allocations[0].status == "CANCELLED"
    assert after.dispatch_blocked
    assert len([e for e in after.ledger if e.kind == "ADJUST"]) == 1
    runtime.apply_event(shortage)
    assert runtime.get("material").ledger == after.ledger


def test_unused_own_reservation_is_reusable_in_replan(tmp_path):
    runtime, planning = material_service(tmp_path)
    current = runtime.get("material")
    result = planning.apply_event(
        event(
            current,
            "delay",
            "DELAY_RECIPE",
            {"recipe_instance_id": current.menu[0].recipe_instance_id, "earliest_start_sec": 120},
        )
    )
    assert result.status == "PUBLISHED", result
    after = runtime.get("material")
    assert after.runtime.details.lots[0].reserved.fraction() == 10
    assert [a.status for a in after.allocations] == ["CANCELLED", "RESERVED"]


def test_failed_partial_consumption_releases_only_unconsumed_reservation(tmp_path):
    runtime, _ = material_service(tmp_path)
    current = runtime.get("material")
    raw = current.runtime.details.lots[0]
    first = min(current.bindings, key=lambda b: b.assignment.interval.start_sec)
    payload = {
        "task_id": first.assignment.task_ids[0],
        "execution_id": "partial",
        "consumed": [
            {
                "lot_id": raw.lot_id,
                "spec_id": raw.spec_id,
                "quantity": {"value": 4, "unit": "g", "scale": 1},
            }
        ],
    }
    assert (
        runtime.apply_event(event(current, "partial-start", "OPERATION_STARTED", payload)).status
        == "APPLIED"
    )
    current = runtime.get("material")
    assert current.runtime.details.lots[0].reserved.fraction() == 6
    failed = event(
        current,
        "partial-failed",
        "OPERATION_FAILED",
        {**payload, "reason": "synthetic failure"},
        at=30,
    )
    assert runtime.apply_event(failed).status == "APPLIED"
    after = runtime.get("material")
    assert after.runtime.details.lots[0].available.fraction() == 6
    assert after.runtime.details.lots[0].reserved.fraction() == 0
    assert after.allocations[0].consumed.fraction() == 4
    assert after.allocations[0].status == "CANCELLED"
    assert len([e for e in after.ledger if e.kind == "CONSUME"]) == 1


def test_completion_without_required_consumption_is_not_accepted_as_qualified(tmp_path):
    runtime, _ = material_service(tmp_path)
    current = runtime.get("material")
    first = min(current.bindings, key=lambda b: b.assignment.interval.start_sec)
    payload = {"task_id": first.assignment.task_ids[0], "execution_id": "unreported"}
    assert (
        runtime.apply_event(event(current, "unreported-start", "OPERATION_STARTED", payload)).status
        == "APPLIED"
    )
    current = runtime.get("material")
    result = runtime.apply_event(
        event(current, "unreported-end", "OPERATION_COMPLETED", payload, at=60)
    )
    assert result.status == "REJECTED", result
    assert runtime.get("material").runtime.executions[0].status == "RUNNING"


def test_publication_failure_rolls_back_new_reservations(tmp_path, monkeypatch):
    runtime, planning = material_service(tmp_path)
    current = runtime.get("material")
    assert (
        runtime.apply_event(
            event(
                current,
                "change",
                "DELAY_RECIPE",
                {
                    "recipe_instance_id": current.menu[0].recipe_instance_id,
                    "earliest_start_sec": 120,
                },
            )
        ).status
        == "APPLIED"
    )
    before = runtime.get("material")

    def interrupted(*args, **kwargs):
        raise RuntimeError("synthetic interruption before notification write")

    monkeypatch.setattr(NotificationService, "persist", interrupted)
    with pytest.raises(RuntimeError, match="synthetic interruption"):
        planning.drain("material")
    assert runtime.get("material") == before
    monkeypatch.undo()
    assert planning.drain("material").status == "PUBLISHED"
    after = runtime.get("material")
    assert after.runtime.details.lots[0].reserved.fraction() == 10
    assert len([a for a in after.allocations if a.status == "RESERVED"]) == 1


def test_simulated_consumption_converts_requirement_to_actual_lot_unit(tmp_path):
    runtime, _ = material_service(tmp_path)
    current = runtime.get("material")
    details = current.runtime.details
    # 明确合成的等量单位投影，仅用于纯反馈适配器，不改写持久化批次单位。
    kilograms = RationalAmount(numerator=1, denominator=100)
    lot = details.lots[0].model_copy(
        update={"unit": "kg", "produced": kilograms, "available": kilograms, "reserved": kilograms}
    )
    projected = current.model_copy(
        update={
            "runtime": current.runtime.model_copy(
                update={"details": details.model_copy(update={"lots": (lot,)})}
            ),
            "allocations": tuple(
                a.model_copy(update={"amount": kilograms}) for a in current.allocations
            ),
        }
    )
    with runtime.store.engine.connect() as tx:
        problem = RuntimeRepository(tx).problem("material", 1)
    first = min(current.bindings, key=lambda b: b.assignment.interval.start_sec)
    payload = simulated_payload(
        projected, problem, first.assignment.task_ids, "unit-conversion", completed=False
    )
    assert len(payload["consumed"]) == 1
    assert payload["consumed"][0].quantity.unit == "kg"
    assert payload["consumed"][0].quantity.value == 1
    assert payload["consumed"][0].quantity.scale == 100
