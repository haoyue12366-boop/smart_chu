"""设备恢复不等于清空；共享资源允许多个独立占用身份。"""

from app.domain.events import DevicePayload, RuntimeEvent
from app.domain.ids import ExecutionId
from app.domain.knowledge import MenuKnowledgeView
from app.domain.runtime_session import RuntimeSession
from app.domain.runtime_snapshot import DeviceState
from app.runtime.continuous_occupation import observed_span, unfinished_members


def apply_device(
    session: RuntimeSession, event: RuntimeEvent, knowledge: MenuKnowledgeView
) -> RuntimeSession:
    payload = event.payload
    assert isinstance(payload, DevicePayload)
    device = next((d for d in knowledge.devices if d.device_instance_id == payload.device_id), None)
    if device is None:
        raise ValueError("设备不属于固定知识版本")
    details = session.runtime.details
    assert details is not None
    states = {d.device_instance_id: d for d in session.runtime.device_states}
    physical_states = [
        state
        for state in states.values()
        if (state.physical_resource_id, state.component_id) == device.competition_key
    ]
    previous = max(physical_states, key=lambda s: s.observed_at, default=None)
    if previous and event.occurred_at < previous.observed_at:
        raise ValueError("过期设备观测不能覆盖新状态")
    occupations = list(details.occupancies)
    records = {record.execution_id: record for record in session.runtime.executions}
    interrupted: set[ExecutionId] = set()
    blocked = set(details.blocked_task_ids)
    if event.event_type in {"DEVICE_UNAVAILABLE", "DEVICE_RELEASE_CONFIRMED"}:
        for item in occupations:
            if (
                item.released_at is None
                and (item.resource.physical_resource_id, item.resource.component_id)
                == device.competition_key
                and (
                    event.event_type == "DEVICE_UNAVAILABLE"
                    or item.execution_id == payload.execution_id
                )
            ):
                blocked.update(unfinished_members(session, item))
    if event.event_type == "DEVICE_UNAVAILABLE":
        interrupted.update(
            item.execution_id
            for item in occupations
            if item.released_at is None
            and (item.resource.physical_resource_id, item.resource.component_id)
            == device.competition_key
        )
    if event.event_type == "DEVICE_RELEASE_CONFIRMED":
        found = False
        for i, item in enumerate(occupations):
            if (
                (item.resource.physical_resource_id, item.resource.component_id)
                == device.competition_key
                and item.execution_id == payload.execution_id
                and item.released_at is None
            ):
                if event.occurred_at < item.started_at:
                    raise ValueError("清空反馈早于实际占用")
                occupations[i] = item.model_copy(
                    update={
                        "released_at": event.occurred_at,
                        "release_event_id": event.event_id.root,
                        "awaiting_confirmation": False,
                    }
                )
                record = records[item.execution_id]
                span = observed_span(session, item, event)
                records[item.execution_id] = record.model_copy(
                    update={
                        "event_refs": (*record.event_refs, event.event_id),
                        "resource_spans": (
                            *record.resource_spans,
                            *((span,) if span else ()),
                        ),
                    }
                )
                interrupted.add(item.execution_id)
                found = True
        if not found:
            raise ValueError("释放确认不属于当前执行占用")
    active = [
        o
        for o in occupations
        if o.released_at is None
        and (o.resource.physical_resource_id, o.resource.component_id) == device.competition_key
    ]
    available = (
        "UNAVAILABLE"
        if event.event_type == "DEVICE_UNAVAILABLE"
        else (
            "AVAILABLE"
            if event.event_type == "DEVICE_RECOVERED"
            else previous.availability_status
            if previous
            else "AVAILABLE"
        )
    )
    physical_state = DeviceState(
        device_instance_id=payload.device_id,
        physical_resource_id=device.competition_key[0],
        component_id=device.competition_key[1],
        availability_status=available,
        occupancy_status="AWAITING_RELEASE_CONFIRMATION"
        if any(o.awaiting_confirmation for o in active)
        else "OCCUPIED"
        if active
        else "FREE",
        active_execution_id=active[0].execution_id if active else None,
        observed_at=event.occurred_at,
        source=event.source,
        configuration=payload.configuration or (previous.configuration if previous else ()),
        expected_recovery_at=payload.expected_recovery_at if available == "UNAVAILABLE" else None,
        release_confirmation_event_id=event.event_id
        if event.event_type == "DEVICE_RELEASE_CONFIRMED"
        else None,
    )
    for alias in knowledge.devices:
        if alias.competition_key == device.competition_key:
            states[alias.device_instance_id] = physical_state.model_copy(
                update={"device_instance_id": alias.device_instance_id}
            )
    for execution_id in interrupted:
        record = records[execution_id]
        if record.status == "RUNNING":
            records[execution_id] = record.model_copy(
                update={
                    "remaining_sec": None,
                    "remaining_source_ref": None,
                    "remaining_observed_at": None,
                    "interruption_event_refs": (*record.interruption_event_refs, event.event_id),
                    "event_refs": tuple(dict.fromkeys((*record.event_refs, event.event_id))),
                }
            )
    return session.model_copy(
        update={
            "runtime": session.runtime.model_copy(
                update={
                    "device_states": tuple(states.values()),
                    "executions": tuple(records.values()),
                    "details": details.model_copy(
                        update={
                            "occupancies": tuple(occupations),
                            "blocked_task_ids": tuple(sorted(blocked, key=lambda task: task.root)),
                        }
                    ),
                }
            )
        }
    )
