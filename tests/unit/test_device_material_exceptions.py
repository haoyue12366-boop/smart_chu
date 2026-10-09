"""合成执行身份配合固定发布设备事实，验证故障与占用分别记账。"""

from app.domain.events import RuntimeEvent
from app.domain.resources import ResourceUse
from app.domain.runtime_facts import Occupancy, RuntimeDetails
from app.domain.runtime_session import RuntimeSession
from app.domain.runtime_snapshot import ExecutionRecord, RuntimeSnapshot
from app.domain.time import TimeOrigin
from app.runtime.device_events import apply_device
from app.runtime.triggers import replan_reasons
from tests.runtime_support import ORIGIN, event, p4_knowledge, policy


def occupied(status="RUNNING"):
    knowledge = p4_knowledge()
    device = next(d for d in knowledge.devices if d.conflict_policy == "STATE_COMPATIBLE")
    alias = device.model_copy(update={"device_instance_id": "synthetic-device-alias"})
    knowledge = knowledge.model_copy(update={"devices": (*knowledge.devices, alias)})
    use = ResourceUse(
        resource_type="DEVICE",
        resource_id=device.device_instance_id,
        physical_resource_id=device.physical_resource_id,
        component_id=device.component_id,
        conflict_policy=device.conflict_policy,
    )
    executions = tuple(
        ExecutionRecord(
            execution_id=f"synthetic-{i}",
            task_ids=(f"synthetic-task-{i}",),
            status=status,
            source="SIMULATED",
            event_refs=(f"synthetic-start-{i}",),
            started_at=ORIGIN,
            finished_at=TimeOrigin(start_at=ORIGIN).at(30) if status == "FAILED" else None,
            resource_ids=(device.device_instance_id,),
            remaining_sec=120,
            remaining_source_ref="synthetic-duration",
            remaining_observed_at=ORIGIN,
        )
        for i in range(2)
    )
    state = RuntimeSnapshot(
        session_id="devices",
        state_revision=0,
        current_plan_version=0,
        knowledge_version=knowledge.release.knowledge_version,
        rule_version=knowledge.release.rule_version,
        snapshot_id=knowledge.release.snapshot_id,
        time_origin=TimeOrigin(start_at=ORIGIN),
        now_offset_sec=30,
        execution_mode="SIMULATED",
        executions=executions,
        details=RuntimeDetails(
            occupancies=tuple(
                Occupancy(
                    occupancy_id=f"synthetic-occupancy-{i}",
                    execution_id=e.execution_id,
                    resource=use,
                    started_at=ORIGIN,
                    awaiting_confirmation=status == "FAILED",
                )
                for i, e in enumerate(executions)
            )
        ),
    )
    return knowledge, device, alias, RuntimeSession(runtime=state, policy=policy(), status="ACTIVE")


def change(session, identity, kind, device, **payload) -> RuntimeEvent:
    return event(
        session, identity, kind, {"device_id": device.device_instance_id, **payload}, at=60
    )


def test_alias_recovery_updates_physical_availability_without_clearing_occupants():
    knowledge, device, alias, session = occupied()
    down = apply_device(
        session,
        change(session, "down", "DEVICE_UNAVAILABLE", device, reason="synthetic fault"),
        knowledge,
    )
    recovered = apply_device(down, change(down, "up", "DEVICE_RECOVERED", alias), knowledge)
    states = [
        s
        for s in recovered.runtime.device_states
        if (s.physical_resource_id, s.component_id) == device.competition_key
    ]
    assert len(states) >= 2
    assert all(s.availability_status == "AVAILABLE" for s in states)
    assert all(o.released_at is None for o in recovered.runtime.details.occupancies)
    assert all(e.remaining_sec is None for e in recovered.runtime.executions)
    assert all(e.interruption_event_refs for e in recovered.runtime.executions)


def test_release_one_shared_occupant_preserves_the_other_and_records_actual_duration():
    knowledge, device, alias, session = occupied("FAILED")
    released = apply_device(
        session,
        change(session, "release", "DEVICE_RELEASE_CONFIRMED", alias, execution_id="synthetic-0"),
        knowledge,
    )
    assert released.runtime.details.occupancies[0].released_at == session.runtime.time_origin.at(60)
    assert released.runtime.details.occupancies[1].released_at is None
    assert all(s.active_execution_id.root == "synthetic-1" for s in released.runtime.device_states)
    assert len(released.runtime.executions[0].resource_spans) == 1
    assert released.runtime.executions[0].resource_spans[0].interval.end_sec == 60
    assert released.runtime.executions[0].event_refs[-1].root == "release"


def test_shared_nonrepresentative_occupant_release_still_triggers_replanning():
    knowledge, device, alias, session = occupied()
    observed = change(session, "observed", "DEVICE_STATE_UPDATED", device)
    before = apply_device(session, observed, knowledge)
    release = change(
        before, "second-release", "DEVICE_RELEASE_CONFIRMED", alias, execution_id="synthetic-1"
    )
    after = apply_device(before, release, knowledge)
    assert (
        before.runtime.device_states[0].active_execution_id
        == after.runtime.device_states[0].active_execution_id
    )
    assert replan_reasons(before, after, release, knowledge) == ("DEVICE_RELEASE_CONFIRMED",)
