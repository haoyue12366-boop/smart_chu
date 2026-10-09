"""把保留的 15 个准备场景逐项执行；真实知识与合成反馈分别注明。"""

import importlib
import itertools
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.domain.events import RuntimeEvent
from app.domain.objectives import ObjectiveStage
from app.domain.runtime_facts import RationalAmount
from app.runtime.clock import SimulationClock
from app.runtime.feedback import FeedbackAdapter
from app.runtime.recovery import restore_session
from app.runtime.service import RuntimeService
from app.runtime.simulated_materials import simulated_payload
from app.scheduling.cp_sat import CpSatScheduler
from app.scheduling.metrics import compute_metrics
from app.scheduling.worker import SolverWorker
from app.services.planning import PlanningService
from app.storage.repositories import RuntimeRepository
from app.storage.unit_of_work import UnitOfWork
from tests.integration.test_inventory_substitution import inventory_bindings, stock_service
from tests.integration.test_material_ledger import material_service
from tests.runtime_support import ORIGIN, event, policy, synthetic_knowledge
from tests.unit.test_total_human_objective import deadline, witness

ROOT = Path(__file__).resolve().parents[2]
CASES = json.loads((ROOT / "data/preparations/p4-v1/runtime_scenarios.json").read_bytes())["cases"]


def manual_runtime(tmp_path):
    knowledge = synthetic_knowledge()
    store = UnitOfWork(tmp_path / "manual-scenario.sqlite")
    store.migrate()
    runtime = RuntimeService(store, knowledge, SimulationClock(ORIGIN))
    session = runtime.create_session("scenario", "MANUAL_CONFIRM", ORIGIN, policy())
    planning = PlanningService(runtime, CpSatScheduler())
    started = planning.apply_event(
        event(
            session,
            "scenario-start",
            "START_SESSION",
            {
                "recipes": [
                    {"id": recipe.recipe_id, "name": recipe.name} for recipe in knowledge.recipes
                ]
            },
        )
    )
    assert started.status == "PUBLISHED", started
    current = runtime.get("scenario")
    mixes = sorted(
        (binding for binding in current.bindings if binding.carrier.duration_sec == 180),
        key=lambda binding: binding.assignment.interval.start_sec,
    )
    return runtime, planning, mixes


def manual_start(runtime, binding, identity, at):
    current = runtime.get("scenario")
    result = runtime.apply_event(
        event(
            current,
            identity + "-start",
            "OPERATION_STARTED",
            {"task_id": binding.assignment.task_ids[0], "execution_id": identity},
            at=at,
        )
    )
    return result


def check_manual_release(case, tmp_path):
    runtime, _, mixes = manual_runtime(tmp_path)
    assert manual_start(runtime, mixes[0], "first", 0).status == "APPLIED"
    current = runtime.get("scenario")
    at = case["input"]["complete_active_at_sec"]
    payload = {"task_id": mixes[0].assignment.task_ids[0], "execution_id": "first"}
    assert (
        runtime.apply_event(
            event(current, "finish-first", "OPERATION_COMPLETED", payload, at=at)
        ).status
        == "APPLIED"
    )
    assert manual_start(runtime, mixes[1], "second", at).status == "APPLIED"
    current = runtime.get("scenario")
    wait = next(
        binding
        for binding in current.bindings
        if binding.carrier.duration_sec == case["input"]["wait_sec"]
        and binding.assignment.interval.start_sec == at
    )
    assert manual_start(runtime, wait, "wait", at).status == "APPLIED"
    current = runtime.get("scenario")
    payload = {"task_id": wait.assignment.task_ids[0], "execution_id": "wait"}
    rejected = runtime.apply_event(
        event(
            current,
            "too-early",
            "OPERATION_COMPLETED",
            payload,
            at=at + case["input"]["wait_sec"] - 1,
        )
    )
    assert rejected.status == "REJECTED"
    current = runtime.get("scenario")
    active = [
        o
        for o in current.runtime.details.occupancies
        if o.resource.resource_type == "HUMAN" and o.released_at is None
    ]
    assert len(active) == 1 and active[0].execution_id.root == "second"


def check_manual_overdue(case, tmp_path):
    runtime, _, mixes = manual_runtime(tmp_path)
    assert manual_start(runtime, mixes[0], "first", 0).status == "APPLIED"
    runtime.clock.advance(case["input"]["now_sec"])
    before = runtime.get("scenario")
    assert FeedbackAdapter().overdue(before, runtime.clock.now())
    assert runtime.get("scenario") == before
    assert before.runtime.executions[0].status == "RUNNING"
    assert any(o.released_at is None for o in before.runtime.details.occupancies)


def check_two_starts(case, tmp_path):
    runtime, _, mixes = manual_runtime(tmp_path)
    assert manual_start(runtime, mixes[0], "first", 0).status == "APPLIED"
    before = runtime.get("scenario")
    conflict = manual_start(runtime, mixes[1], "second", 0)
    assert conflict.status == "CONFLICT"
    after = runtime.get("scenario")
    assert after.runtime.executions == before.runtime.executions
    assert after.dispatch_blocked
    active = [o for o in after.runtime.details.occupancies if o.released_at is None]
    assert len(active) == case["input"]["overlapping_starts"] - 1
    assert active[0].resource.resource_id == case["input"]["resource_id"]


def check_duplicate_completion(case, tmp_path):
    runtime, planning = material_service(tmp_path)
    current = runtime.get("material")
    binding = min(current.bindings, key=lambda item: item.assignment.interval.start_sec)
    with runtime.store.engine.connect() as tx:
        problem = RuntimeRepository(tx).problem("material", 1)
    group = binding.assignment.task_ids
    payload = simulated_payload(current, problem, group, "duplicate-run", completed=False)
    assert (
        runtime.apply_event(event(current, "run-start", "OPERATION_STARTED", payload)).status
        == "APPLIED"
    )
    current = runtime.get("material")
    payload = simulated_payload(current, problem, group, "duplicate-run", completed=True)
    completed = event(
        current, "run-end", "OPERATION_COMPLETED", payload, at=binding.assignment.interval.end_sec
    )
    first = planning.apply_event(completed)
    assert first.status == "NO_REPLAN", first
    after = runtime.get("material")
    for _ in range(case["input"]["same_event_repetitions"] - 1):
        replay = planning.apply_event(completed)
        assert replay.status == "NO_REPLAN" and replay.event == first.event
        assert runtime.get("material") == after
    assert len([entry for entry in after.ledger if entry.kind == "CONSUME"]) == 1
    assert all(o.released_at is not None for o in after.runtime.details.occupancies)


def check_qualitative_inventory(case, tmp_path):
    runtime, planning, _, lot, _ = stock_service(tmp_path)
    current = runtime.get("inventory")
    qualitative = lot.model_copy(
        update={
            "quantity_kind": "RECIPE_BATCH",
            "unit": None,
            "produced": RationalAmount(numerator=1),
            "available": RationalAmount(numerator=1),
            "reserved": RationalAmount(numerator=0),
        }
    )
    details = current.runtime.details.model_copy(
        update={
            "lots": tuple(
                qualitative if item.lot_id == lot.lot_id else item
                for item in current.runtime.details.lots
            )
        }
    )
    with runtime.store.transaction() as tx:
        RuntimeRepository(tx).save(
            current.model_copy(
                update={"runtime": current.runtime.model_copy(update={"details": details})}
            ),
            expected_revision=current.runtime.state_revision,
        )
    assert case["input"]["requested_grams"] == 50
    outcome = planning.drain("inventory")
    assert outcome.status in {"PUBLISHED", "FAILED"}, outcome
    assert not inventory_bindings(runtime)
    observed = next(
        item for item in runtime.get("inventory").runtime.details.lots if item.lot_id == lot.lot_id
    )
    assert observed.quantity_kind == "RECIPE_BATCH" and observed.available.fraction() == 1


def check_manual_restart(case, tmp_path):
    runtime, _, mixes = manual_runtime(tmp_path)
    assert manual_start(runtime, mixes[0], "first", 0).status == "APPLIED"
    before = runtime.get("scenario")
    knowledge, path = runtime.knowledge, runtime.store.path
    runtime.store.close()
    clock = SimulationClock(ORIGIN)
    clock.advance(300)
    seen = []

    def load(ref):
        seen.append(ref)
        return knowledge

    restored = restore_session(UnitOfWork(path), "scenario", clock, load)
    assert restored.get("scenario") == before
    assert before.runtime.execution_mode == case["input"]["execution_mode"]
    assert before.runtime.executions[0].status == "RUNNING"
    assert seen == [knowledge.release]
    assert any(o.released_at is None for o in before.runtime.details.occupancies)


def check_human_objectives(case, tmp_path):
    problem = witness()
    shared_sec = case["input"]["option_a"]["total_human_sec"]
    single_sec = case["input"]["option_b"]["total_human_sec"] // 2
    cap = 900
    candidates = tuple(
        c.model_copy(update={"duration_sec": single_sec if c.resource_uses else cap})
        for c in problem.standalone_candidates
    )
    problem = problem.model_copy(
        update={
            "horizon_sec": 2400,
            "logical_tasks": tuple(
                t.model_copy(update={"latest_end_sec": 2400}) for t in problem.logical_tasks
            ),
            "standalone_candidates": candidates,
            "shared_prep_candidates": (
                problem.shared_prep_candidates[0].model_copy(update={"duration_sec": shared_sec}),
            ),
        }
    )
    options = []
    for lengths in ((shared_sec,), (single_sec, single_sec)):
        for starts in itertools.product(range(0, cap + 1, 60), repeat=len(lengths)):
            spans = sorted(
                (start, start + length) for start, length in zip(starts, lengths, strict=True)
            )
            if spans[-1][1] > cap or any(
                left[1] > right[0] for left, right in zip(spans, spans[1:], strict=False)
            ):
                continue
            blocks = []
            for start, end in spans:
                if blocks and start - blocks[-1][1] < 60:
                    blocks[-1] = (blocks[-1][0], end)
                else:
                    blocks.append((start, end))
            options.append((sum(lengths), max(end - start for start, end in blocks)))
    assert min(options) == (480, 480)
    assert min(options, key=lambda pair: pair[::-1]) == (600, 300)
    solver = CpSatScheduler()
    total = solver.solve(
        problem, None, deadline(), stage=ObjectiveStage(name="D_TOTAL_HUMAN", makespan_cap_sec=cap)
    )
    busy = solver.solve(
        problem, None, deadline(), stage=ObjectiveStage(name="D_HUMAN", makespan_cap_sec=cap)
    )
    assert total.status == busy.status == "OPTIMAL"
    assert total.objective_value == 480 and busy.objective_value == 300
    assert compute_metrics(total.candidate, problem).total_human_work_sec == 480
    assert compute_metrics(busy.candidate, problem).total_human_work_sec == 600


def check_missing_retry_rule(case, tmp_path):
    runtime, planning, mixes = manual_runtime(tmp_path)
    assert manual_start(runtime, mixes[0], "first", 0).status == "APPLIED"
    current = runtime.get("scenario")
    payload = {
        "execution_id": "first",
        "task_id": mixes[0].assignment.task_ids[0],
        "reason": "synthetic:failed",
        "output_status": "UNKNOWN",
    }
    assert (
        runtime.apply_event(event(current, "failure", "OPERATION_FAILED", payload, at=30)).status
        == "APPLIED"
    )
    before = runtime.get("scenario")
    raw = event(
        before, "placeholder", "RESET_SESSION", {"reason": "placeholder"}, at=30
    ).model_dump(mode="json")
    raw.update(
        event_type=case["input"]["event_type"],
        payload={
            "failed_execution_id": "first",
            "new_execution_id": "second",
            "task_id": mixes[0].assignment.task_ids[0].root,
            "recovery_rule_id": case["input"]["recovery_rule_id"],
            "consumed": [],
        },
    )
    with pytest.raises(ValidationError):
        RuntimeEvent.model_validate(raw)
    assert runtime.get("scenario") == before
    assert planning.drain("scenario").status == "FAILED"
    assert runtime.get("scenario").runtime.executions == before.runtime.executions


EXISTING = {
    "real-marinade-chain": (
        "test_real_runtime_flow",
        "test_real_marinade_chain_has_material_evidence_and_no_human_during_wait",
    ),
    "marinade-add-recipe": (
        "test_replan_freezing",
        "test_elapsed_marinade_kept_and_active_human_released",
    ),
    "device-recovered-not-released": (
        "test_device_and_material_failures",
        "test_running_alias_fault_and_recovery_preserve_unknown_result_and_occupation",
    ),
    "event-during-solving": (
        "test_menu_events_replanning",
        "test_event_commits_during_solve_and_latest_revision_is_coalesced",
    ),
    "notification-cancel": (
        "test_notification_outbox",
        "test_replan_cancels_pending_and_keeps_sent_records",
    ),
}
CHECKS = {
    "manual-marinade-release": check_manual_release,
    "manual-overdue": check_manual_overdue,
    "two-human-starts": check_two_starts,
    "duplicate-completion": check_duplicate_completion,
    "qualitative-inventory": check_qualitative_inventory,
    "restart-no-auto-complete": check_manual_restart,
    "total-vs-continuous-human": check_human_objectives,
    "retry-without-rule": check_missing_retry_rule,
}


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["case_id"])
def test_prepared_runtime_scenario(case, tmp_path):
    identity = case["case_id"]
    if identity in CHECKS:
        CHECKS[identity](case, tmp_path)
    elif identity in EXISTING:
        module, name = EXISTING[identity]
        getattr(importlib.import_module("tests.integration." + module), name)(tmp_path)
    elif identity == "hot-batch-add-member":
        from tests.integration.test_running_thermal_batch import (
            test_h02_running_batch_keeps_members_when_new_recipe_is_added,
        )

        with SolverWorker() as worker:
            test_h02_running_batch_keeps_members_when_new_recipe_is_added(tmp_path, worker)
    elif identity == "same-event-id-different-payload":
        from tests.unit.test_execution_state_machine import (
            test_duplicate_event_before_version_check_and_changed_payload_conflicts,
        )

        test_duplicate_event_before_version_check_and_changed_payload_conflicts(tmp_path)
    else:
        pytest.fail("准备场景尚未接入执行：" + identity)
