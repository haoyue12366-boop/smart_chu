"""明确合成的载体与设备例外；验证在途冻结、层位、失败及恢复。"""

from datetime import timedelta

from app.domain.events import EventSource
from app.domain.ids import CarrierId
from app.domain.knowledge import EvidenceIndexEntry
from app.domain.resources import DeviceInstance, DeviceProfile, ParameterConstraint, ResourceUse
from app.domain.runtime_session import ExecutionBinding
from app.domain.schedule import ScheduledAssignment
from app.domain.scheduling_problem import CandidateCarrier
from app.domain.time import Interval
from app.runtime.clock import SimulationClock
from app.runtime.service import RuntimeService
from app.scheduling.cp_sat import CpSatScheduler
from app.services.planning import PlanningService
from app.storage.repositories import RuntimeRepository
from app.storage.unit_of_work import UnitOfWork
from tests.integration.test_schedule_clock import (
    clock_knowledge,
    clock_service,
    request_replan,
    tick,
)
from tests.runtime_support import ORIGIN, event


def test_legacy_pending_boundary_restarts_into_immediate_replan(tmp_path):
    runtime, planner, _ = clock_service(tmp_path)
    before = tick(runtime, 60)
    assert (
        runtime.apply_event(event(before, "legacy-request", "REPLAN_REQUESTED", {}, at=60)).status
        == "APPLIED"
    )
    legacy = runtime.get("clock")
    with runtime.store.transaction() as tx:
        RuntimeRepository(tx).save(
            legacy.model_copy(
                update={
                    "schedule_clock": legacy.schedule_clock.model_copy(
                        update={"replan_not_before_sec": 120}
                    )
                }
            ),
            expected_revision=legacy.runtime.state_revision,
        )
    knowledge = runtime.knowledge
    runtime.store.close()
    store = UnitOfWork(tmp_path / "clock.sqlite")
    store.migrate()
    wall = SimulationClock(ORIGIN)
    wall.advance(90)
    recovered = RuntimeService(store, knowledge, wall)
    resumed = PlanningService(recovered, CpSatScheduler())
    pending = resumed.drain("clock")
    assert pending.status == "PUBLISHED", pending
    assert pending.plan.plan_version == 2
    current = recovered.get("clock")
    assert current.runtime.executions == before.runtime.executions
    assert current.schedule_clock.started_at == before.schedule_clock.started_at
    wall.advance(120)
    assert resumed.drain("clock").status == "NO_REPLAN"
    assert recovered.get("clock").runtime.executions[0].finished_at == ORIGIN + timedelta(
        seconds=120
    )
    store.close()


def test_active_multi_stage_carrier_retains_necessary_stages_after_immediate_replan(tmp_path):
    runtime, planner, _ = clock_service(tmp_path)
    session = runtime.get("clock")
    bindings = sorted(session.bindings, key=lambda b: b.assignment.interval.start_sec)
    tasks = tuple(t for binding in bindings for t in binding.assignment.task_ids)
    spans = tuple(s for binding in bindings for s in binding.task_spans)
    # 此运行时合成夹具显式组合已发布工序；不作为知识发布或编译端到端验收。
    carrier = CandidateCarrier.model_validate(
        {
            "carrier_id": "synthetic-multistage",
            "kind": "THERMAL_BATCH",
            "covers": tasks,
            "duration_sec": 420,
            "member_offsets": [
                {
                    "task_id": p.task_id,
                    "start_offset_sec": p.interval.start_sec,
                    "end_offset_sec": p.interval.end_sec,
                }
                for p in spans
            ],
            "resource_phases": [
                {
                    "task_id": b.assignment.task_ids[0],
                    "start_offset_sec": b.assignment.interval.start_sec,
                    "end_offset_sec": b.assignment.interval.end_sec,
                    "resource_use": use,
                }
                for b in bindings
                for use in b.assignment.resource_uses
            ],
        }
    )
    merged = ExecutionBinding(
        assignment=ScheduledAssignment(
            carrier_id=CarrierId("synthetic-multistage"),
            task_ids=tasks,
            interval=Interval(start_sec=0, end_sec=420),
            resource_uses=(),
        ),
        carrier=carrier,
        plan_version=1,
        task_spans=spans,
    )
    with runtime.store.transaction() as tx:
        RuntimeRepository(tx).save(
            session.model_copy(update={"bindings": (merged,)}),
            expected_revision=session.runtime.state_revision,
        )
    tick(runtime, 60)
    waiting = request_replan(runtime, planner, 60)
    assert waiting.status == "PUBLISHED", waiting
    at120 = tick(runtime, 120)
    assert at120.runtime.executions[0].status == "RUNNING"
    assert len(at120.runtime.executions[0].started_task_ids) == 2
    assert len(at120.runtime.executions[0].completed_task_ids) == 1
    assert planner.drain("clock").status == "NO_REPLAN"
    at360 = tick(runtime, 360)
    assert len(at360.runtime.executions[0].started_task_ids) == 3
    at420 = tick(runtime, 420)
    assert at420.runtime.executions[0].status == "COMPLETED"
    assert len(at420.runtime.executions[0].completed_task_ids) == 3
    assert len([e for e in at420.ledger if e.kind == "CONSUME"]) == 3
    runtime.store.close()


def layered_runtime(tmp_path, *, tight=False):
    from app.domain.policy import ObjectiveSpec, SchedulingPolicy

    source = clock_knowledge(parallel=True)
    if tight:
        recipe = source.recipes[0]
        first = recipe.operations[0]
        follow = first.model_copy(
            update={
                "operation_id": "finish",
                "duration": first.duration.model_copy(update={"execution_sec": 60}),
                "material_inputs": (),
                "material_outputs": (),
            }
        )
        changed = type(recipe).model_validate(
            {
                **recipe.model_dump(),
                "operations": (first, follow),
                "dependencies": [
                    {
                        "predecessor_id": first.operation_id,
                        "successor_id": follow.operation_id,
                        "min_lag_sec": 0,
                        "max_lag_sec": 0,
                        "reason": "合成不可中断工艺链",
                        "evidence_refs": ["synthetic:clock"],
                    }
                ],
            }
        )
        source = source.model_copy(update={"recipes": (changed, source.recipes[1])})
    use = ResourceUse.model_validate(
        {
            "resource_type": "DEVICE",
            "resource_id": "steam_oven_1",
            "physical_resource_id": "steam_oven_1",
            "component_id": "cavity",
            "conflict_policy": "STATE_COMPATIBLE",
            "review_status": "APPROVED",
            "rule_version": "synthetic-clock-v1",
            "evidence_refs": ["synthetic:clock"],
            "configuration": [
                {"parameter": "temperature_c", "value": 100},
                {"parameter": "mode", "value": "steam"},
            ],
        }
    )
    source = source.model_copy(
        update={
            "recipes": tuple(
                r.model_copy(
                    update={
                        "operations": tuple(
                            op.model_copy(update={"resource_requirements": (use,)})
                            for op in r.operations
                        )
                    }
                )
                for r in source.recipes
            ),
            "devices": (
                DeviceInstance(
                    device_instance_id="steam_oven_1",
                    physical_resource_id="steam_oven_1",
                    component_id="cavity",
                    capacity=3,
                    conflict_policy="STATE_COMPATIBLE",
                    review_status="APPROVED",
                    rule_version="synthetic-clock-v1",
                    evidence_refs=("synthetic:clock",),
                    capability_refs=("synthetic-steam",),
                ),
            ),
            "profiles": (
                DeviceProfile(
                    profile_id="synthetic-steam",
                    device_type="steam",
                    mode="steam",
                    rule_version="synthetic-clock-v1",
                    provenance_refs=("synthetic:clock",),
                    review_status="APPROVED",
                    constraints=(
                        ParameterConstraint(parameter="temperature_c", minimum=100, maximum=100),
                    ),
                ),
            ),
            "provenance_index": (
                EvidenceIndexEntry(
                    evidence_id="synthetic:clock",
                    artifact_path="synthetic-test-fixture",
                    artifact_hash="0" * 64,
                    locator="tests/integration/test_schedule_clock_safety.py",
                ),
            ),
        }
    )
    store = UnitOfWork(tmp_path / "layer-clock.sqlite")
    store.migrate()
    runtime = RuntimeService(store, source, SimulationClock(ORIGIN))
    current = runtime.create_session(
        "clock",
        "SCHEDULE_CLOCK",
        ORIGIN,
        SchedulingPolicy(policy_version="clock", objective=ObjectiveSpec(stages=("MAKESPAN",))),
    )
    planner = PlanningService(runtime, CpSatScheduler())
    published = planner.apply_event(
        event(
            current,
            "start",
            "START_SESSION",
            {
                "recipes": [{"id": r.recipe_id, "name": r.name} for r in source.recipes],
            },
        )
    )
    assert published.status == "PUBLISHED", published.model_dump_json()
    return runtime, planner


def test_immediate_replan_keeps_layer_until_its_own_clock_completion(tmp_path):
    runtime, planner = layered_runtime(tmp_path)
    current = tick(runtime, 60)
    occupied = [item for item in current.runtime.details.occupancies if item.released_at is None]
    assert len(occupied) == 2
    assert len({item.resource.layer_index for item in occupied}) == 2
    layers = {item.execution_id: item.resource.layer_index for item in occupied}
    assert request_replan(runtime, planner, 60).status == "PUBLISHED"
    after = tick(runtime, 120)
    held = [item for item in after.runtime.details.occupancies if item.released_at is None]
    assert len(held) == 1
    assert held[0].resource.layer_index == layers[held[0].execution_id]
    tick(runtime, 180)
    assert planner.drain("clock").status == "NO_REPLAN"
    assert all(
        item.released_at is not None for item in runtime.get("clock").runtime.details.occupancies
    )
    runtime.store.close()


def test_device_failure_stops_clock_completion_and_reports_required_feedback(tmp_path):
    runtime, planner = layered_runtime(tmp_path)
    current = tick(runtime, 60)
    failure = event(
        current,
        "device-failure",
        "DEVICE_UNAVAILABLE",
        {
            "device_id": "steam_oven_1",
            "reason": "合成断电",
        },
        at=60,
    ).model_copy(update={"source": EventSource.DEVICE_FEEDBACK})
    outcome = planner.apply_event(failure)
    assert outcome.event.status == "APPLIED"
    assert outcome.status == "FAILED"
    assert outcome.planning.failure.code == "STATE_INCOMPLETE"
    after = tick(runtime, 300)
    assert all(record.status == "RUNNING" for record in after.runtime.executions)
    assert not any(record.completed_task_ids for record in after.runtime.executions)
    assert all(item.released_at is None for item in after.runtime.details.occupancies)
    assert after.dispatch_blocked
    assert after.runtime.current_plan_version == 1
    runtime.store.close()


def test_immediate_replan_preserves_mandatory_zero_gap_continuation_of_started_work(tmp_path):
    runtime, planner = layered_runtime(tmp_path, tight=True)
    before = tick(runtime, 60)
    assert len(before.runtime.executions) == 2
    waiting = request_replan(runtime, planner, 60)
    assert waiting.status == "PUBLISHED", waiting
    at120 = tick(runtime, 120)
    assert len(at120.runtime.executions) == 3, "即时重排不能截断已开始工艺的零间隔衔接"
    assert sum(e.status == "RUNNING" for e in at120.runtime.executions) == 2
    tick(runtime, 180)
    replanned = planner.drain("clock")
    assert replanned.status == "NO_REPLAN", replanned
    assert all(e.status == "COMPLETED" for e in runtime.get("clock").runtime.executions)
    runtime.store.close()
