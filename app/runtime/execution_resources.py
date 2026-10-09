"""实际占用与计划内部阶段分离；物理竞争键跨能力别名保持一致。"""

from app.domain.events import RuntimeEvent
from app.domain.ids import ExecutionId, TaskId
from app.domain.resources import DeviceInstance, ResourceUse
from app.domain.runtime_facts import ActualResourceSpan, Occupancy
from app.domain.runtime_session import ExecutionBinding, RuntimeSession
from app.runtime.continuous_occupation import observed_span, unfinished_members
from app.runtime.event_validation import EventConflict


def group_uses(uses: tuple[ResourceUse, ...]) -> tuple[ResourceUse, ...]:
    """共享主动阶段只申请一次物理资源，并保留全部成员来源。"""
    combined: dict[tuple[str, str], ResourceUse] = {}
    for use in uses:
        key = (use.physical_resource_id or use.resource_id, use.component_id or use.resource_id)
        previous = combined.get(key)
        if previous is None:
            combined[key] = use
            continue
        if previous.model_dump(exclude={"evidence_refs"}) != use.model_dump(
            exclude={"evidence_refs"}
        ):
            raise EventConflict("同一主动阶段对同一物理资源提出不同配置")
        combined[key] = previous.model_copy(
            update={
                "evidence_refs": tuple(dict.fromkeys((*previous.evidence_refs, *use.evidence_refs)))
            }
        )
    return tuple(combined.values())


def conflict(a: ResourceUse, b: ResourceUse) -> bool:
    if (a.physical_resource_id or a.resource_id, a.component_id or a.resource_id) != (
        b.physical_resource_id or b.resource_id,
        b.component_id or b.resource_id,
    ):
        return False
    if a.conflict_policy in {None, "UNARY", "BATCH_EXCLUSIVE"} or b.conflict_policy in {
        None,
        "UNARY",
        "BATCH_EXCLUSIVE",
    }:
        return True
    if set(a.effective_layer_indices) & set(b.effective_layer_indices):
        return True
    left = {v.parameter: v.value for v in a.configuration if v.parameter != "duration_sec"}
    right = {v.parameter: v.value for v in b.configuration if v.parameter != "duration_sec"}
    if a.effective_layer_indices or b.effective_layer_indices:
        return (
            "temperature_c" not in left
            or "mode" not in left
            or "temperature_c" not in right
            or "mode" not in right
            or left != right
        )
    return any(left[p] != right[p] for p in left.keys() & right.keys())


def acquire(
    session: RuntimeSession,
    event: RuntimeEvent,
    execution_id: ExecutionId,
    uses: tuple[ResourceUse, ...],
    binding: ExecutionBinding,
    devices: tuple[DeviceInstance, ...] = (),
) -> tuple[tuple[Occupancy, ...], tuple[tuple[ExecutionId, ActualResourceSpan | None], ...]]:
    assert session.runtime.details is not None
    occupations = list(session.runtime.details.occupancies)
    transfers = []
    records = {record.execution_id: record for record in session.runtime.executions}
    known_devices = {device.device_instance_id: device for device in devices}
    for use in uses:
        active = [o for o in occupations if o.released_at is None]
        device = known_devices.get(use.resource_id)
        if (
            device is not None
            and device.capacity > 1
            and (
                not use.effective_layer_indices
                or any(layer > device.capacity for layer in use.effective_layer_indices)
                or len(use.effective_layer_indices) != use.units
                or use.units > device.capacity
            )
        ):
            raise ValueError("设备层位缺失或超出已发布容量")
        if (
            device is not None
            and device.capacity > 1
            and use.conflict_policy == "STATE_COMPATIBLE"
            and not {"temperature_c", "mode"} <= {value.parameter for value in use.configuration}
        ):
            raise ValueError("分层设备必须明确共同温度与模式")
        if any(
            o.execution_id == execution_id
            and o.resource.model_dump(exclude={"evidence_refs"})
            == use.model_dump(exclude={"evidence_refs"})
            for o in active
        ):
            continue
        if use.resource_type == "DEVICE" and any(
            (d.physical_resource_id, d.component_id) == (use.physical_resource_id, use.component_id)
            and d.availability_status != "AVAILABLE"
            for d in session.runtime.device_states
        ):
            raise ValueError("设备尚未确认可用")
        scopes = [
            scope for scope in binding.continuities if use.resource_id in scope.resource_options
        ]
        if len(scopes) > 1:
            raise ValueError("设备实际占用对应多个重叠的连续工艺范围")
        scope = scopes[0] if scopes else None
        for item in active:
            if item.execution_id == execution_id and conflict(item.resource, use):
                if item.resource.model_dump(exclude={"evidence_refs"}) == use.model_dump(
                    exclude={"evidence_refs"}
                ):
                    continue
                raise EventConflict("运行中同一物理资源的配置尚未确认转换")
            same_continuity = (
                scope is not None
                and item.reservation_id == scope.reservation_id
                and (item.resource.physical_resource_id, item.resource.component_id)
                == (use.physical_resource_id, use.component_id)
            )
            if (
                same_continuity
                and item.resource.effective_layer_indices != use.effective_layer_indices
            ):
                raise EventConflict("连续工艺交接必须保持原设备层位")
            if not conflict(item.resource, use) and not same_continuity:
                continue
            owner = records[item.execution_id]
            if (
                scope is None
                or item.reservation_id != scope.reservation_id
                or set(item.reservation_members) != set(scope.members)
                or not set(owner.task_ids) <= set(scope.members)
                or owner.status != "COMPLETED"
                or item.awaiting_confirmation
                or owner.finished_at is None
                or event.occurred_at < owner.finished_at
            ):
                raise EventConflict("实际资源已被其他执行占用")
            transfers.append((item.execution_id, observed_span(session, item, event)))
            occupations[occupations.index(item)] = item.model_copy(
                update={
                    "released_at": event.occurred_at,
                    "release_event_id": event.event_id.root,
                }
            )
        if device is not None and device.capacity > 1:
            held = [
                item
                for item in occupations
                if item.released_at is None
                and (item.resource.physical_resource_id, item.resource.component_id)
                == device.competition_key
            ]
            if sum(item.resource.units for item in held) + use.units > device.capacity:
                raise EventConflict("实际在场任务已占满设备层位")
        occupations.append(
            Occupancy(
                occupancy_id=f"{execution_id.root}:{use.resource_id}:{event.event_id.root}",
                execution_id=execution_id,
                resource=use,
                started_at=event.occurred_at,
                reservation_id=scope.reservation_id if scope else None,
                reservation_members=scope.members if scope else (),
            )
        )
    return tuple(occupations), tuple(transfers)


def release(
    session: RuntimeSession,
    event: RuntimeEvent,
    execution_id: ExecutionId,
    *,
    device_confirmed: bool,
    terminal: bool,
    completing: tuple[TaskId, ...] = (),
) -> tuple[tuple[Occupancy, ...], tuple[ActualResourceSpan, ...]]:
    assert session.runtime.details is not None
    occupations = []
    spans = []
    for item in session.runtime.details.occupancies:
        if item.execution_id == execution_id and item.released_at is None:
            should_release = item.resource.resource_type == "HUMAN" or (
                terminal and device_confirmed
            )
            span = observed_span(session, item, event)
            if span is not None:
                spans.append(span)
            if should_release:
                item = item.model_copy(
                    update={
                        "released_at": event.occurred_at,
                        "release_event_id": event.event_id.root,
                        "awaiting_confirmation": False,
                    }
                )
            elif terminal:
                continuing = event.event_type == "OPERATION_COMPLETED" and bool(
                    unfinished_members(session, item, completing)
                )
                item = item.model_copy(update={"awaiting_confirmation": not continuing})
        occupations.append(item)
    return tuple(occupations), tuple(spans)
