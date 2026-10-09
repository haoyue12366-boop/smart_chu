"""架构 16.9.4 七类案例：真实算法与 SQLite，恢复依据显式为合成。"""

import pytest
from sqlalchemy import select

from app.domain.events import RuntimeEvent
from app.scheduling.worker import SolverWorker
from app.storage import models
from tests.integration.test_failed_execution_retry import shared_failure
from tests.integration.test_material_ledger import material_service
from tests.integration.test_running_thermal_batch import running_h02
from tests.runtime_support import event

CASES = (
    "shared-preparation-failed",
    "oven-unavailable-not-cleared",
    "recovered-result-still-unknown",
    "stock-below-reservation",
    "duplicate-failure",
    "remake-has-two-execution-identities",
    "replan-failure-preserves-exception-facts",
)


def actual_thermal_fault(tmp_path):
    with SolverWorker() as worker:
        runtime, planning, _, binding, running, _ = running_h02(tmp_path, worker)
    use = next(use for use in binding.carrier.resource_uses if use.resource_type == "DEVICE")
    assert use.physical_resource_id == "steam_oven_1"
    before = runtime.get("flow")
    at = runtime.clock.offset_sec
    down = planning.apply_event(
        event(
            before,
            "oven-down",
            "DEVICE_UNAVAILABLE",
            {
                "device_id": use.resource_id,
                "reason": "synthetic:故障注入",
            },
            at=at,
        )
    )
    assert down.event.status == "APPLIED" and down.status == "FAILED"
    assert down.planning.failure.code == "STATE_INCOMPLETE"
    faulted = runtime.get("flow")
    assert any(
        state.availability_status == "UNAVAILABLE" for state in faulted.runtime.device_states
    )
    assert any(
        o.execution_id == running.execution_id and o.released_at is None
        for o in faulted.runtime.details.occupancies
    )
    assert (
        next(e for e in faulted.runtime.executions if e.execution_id == running.execution_id).status
        == "RUNNING"
    )
    return runtime, planning, use, running, faulted


@pytest.mark.parametrize("case_id", CASES)
def test_architecture_recovery_case(case_id, tmp_path):
    if case_id in {"shared-preparation-failed", "duplicate-failure"}:
        runtime, planning, _, original = shared_failure(tmp_path)
        before = runtime.get("shared-retry")
        failed = next(
            e for e in before.runtime.executions if e.execution_id == original.execution_id
        )
        assert failed.status == "FAILED" and len(failed.task_ids) == 2
        assert all(lot.available.fraction() == 0 for lot in before.runtime.details.lots)
        assert len([entry for entry in before.ledger if entry.kind == "CONSUME"]) == 2
        with runtime.store.engine.connect() as tx:
            body = tx.execute(
                select(models.events.c.body).where(models.events.c.event_id == "shared-failed")
            ).scalar_one()
        replayed = runtime.apply_event(RuntimeEvent.model_validate_json(body))
        assert replayed.status == "APPLIED"
        assert runtime.get("shared-retry") == before
        assert planning.drain("shared-retry").status == "FAILED"
        assert runtime.get("shared-retry").runtime.executions == before.runtime.executions
    elif case_id in {"oven-unavailable-not-cleared", "recovered-result-still-unknown"}:
        runtime, planning, use, running, faulted = actual_thermal_fault(tmp_path)
        if case_id == "recovered-result-still-unknown":
            up = planning.apply_event(
                event(
                    faulted,
                    "oven-up",
                    "DEVICE_RECOVERED",
                    {"device_id": use.resource_id},
                    at=runtime.clock.offset_sec,
                )
            )
            assert up.event.status == "APPLIED" and up.status == "FAILED"
            after = runtime.get("flow")
            assert after.runtime.executions == faulted.runtime.executions
            assert after.runtime.details.occupancies == faulted.runtime.details.occupancies
            assert after.dispatch_blocked
            observed = next(
                e for e in after.runtime.executions if e.execution_id == running.execution_id
            )
            assert observed.remaining_sec is None and observed.interruption_event_refs
    elif case_id == "stock-below-reservation":
        runtime, planning = material_service(tmp_path)
        before = runtime.get("material")
        binding = min(before.bindings, key=lambda item: item.assignment.interval.start_sec)
        raw = before.runtime.details.lots[0]
        started = runtime.apply_event(
            event(
                before,
                "partial-input",
                "OPERATION_STARTED",
                {
                    "execution_id": "partial",
                    "task_id": binding.assignment.task_ids[0],
                    "consumed": [
                        {
                            "lot_id": raw.lot_id,
                            "spec_id": raw.spec_id,
                            "quantity": {"value": 3, "unit": "g", "scale": 1},
                        }
                    ],
                },
            )
        )
        assert started.status == "APPLIED", started
        before = runtime.get("material")
        assert before.runtime.details.lots[0].available.fraction() == 7
        consumed = tuple(entry for entry in before.ledger if entry.kind == "CONSUME")
        shortage = event(
            before,
            "measured-shortage",
            "MATERIAL_SHORTAGE",
            {
                "lot_id": raw.lot_id,
                "before": {"value": 7, "unit": "g", "scale": 1},
                "after": {"value": 6, "unit": "g", "scale": 1},
                "reason": "synthetic:实有量低于预约",
                "evidence_refs": ["synthetic:盘点"],
            },
            at=10,
        )
        outcome = planning.apply_event(shortage)
        assert outcome.event.status == "APPLIED", outcome
        assert outcome.status == "FAILED", outcome
        assert outcome.planning.failure.code == "STATE_INCOMPLETE"
        after = runtime.get("material")
        assert after.dispatch_blocked and after.runtime.current_plan_version == 1
        assert tuple(entry for entry in after.ledger if entry.kind == "CONSUME") == consumed
        assert after.runtime.details.lots[0].available.fraction() == 6
        assert after.runtime.details.lots[0].reserved.fraction() == 0
        assert all(
            item.status != "RESERVED" for item in after.allocations if item.lot_id == raw.lot_id
        )
        assert after.runtime.executions[0].consumed == before.runtime.executions[0].consumed
    elif case_id == "remake-has-two-execution-identities":
        from tests.integration.test_failed_execution_retry import (
            test_shared_failure_remake_keeps_members_and_never_restores_consumed_raw,
        )

        test_shared_failure_remake_keeps_members_and_never_restores_consumed_raw(tmp_path)
    else:
        runtime, planning = material_service(tmp_path)
        before = runtime.get("material")
        down = runtime.apply_event(
            event(
                before,
                "unavailable",
                "DEVICE_UNAVAILABLE",
                {"device_id": "steam_oven_1", "reason": "synthetic:故障"},
            )
        )
        assert down.status == "APPLIED"
        before = runtime.get("material")
        raw = before.runtime.details.lots[0]
        shortage = event(
            before,
            "exception-shortage",
            "MATERIAL_SHORTAGE",
            {
                "lot_id": raw.lot_id,
                "before": {"value": 10, "unit": "g", "scale": 1},
                "after": {"value": 9, "unit": "g", "scale": 1},
                "reason": "synthetic:盘点",
                "evidence_refs": ["synthetic:盘点"],
            },
        )
        outcome = planning.apply_event(shortage)
        assert outcome.event.status == "APPLIED" and outcome.status == "FAILED", outcome
        after = runtime.get("material")
        assert any(
            state.device_instance_id == "steam_oven_1"
            and state.availability_status == "UNAVAILABLE"
            for state in after.runtime.device_states
        )
        assert after.runtime.details.lots[0].available.fraction() == 9
        assert after.dispatch_blocked and after.runtime.current_plan_version == 1
        assert any(entry.kind == "ADJUST" for entry in after.ledger)
