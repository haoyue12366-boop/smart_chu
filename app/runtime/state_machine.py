"""统一处理显式执行事件；时钟推断保留来源，共同载体按阶段落账。"""

from app.domain.events import ExecutionPayload, RuntimeEvent
from app.domain.material import MaterialSpec
from app.domain.resources import DeviceInstance
from app.domain.runtime_facts import ActualResourceSpan, TaskSpan
from app.domain.runtime_session import ExecutionBinding, RuntimeSession
from app.domain.runtime_snapshot import ExecutionRecord, ExecutionStatus
from app.domain.time import Interval
from app.runtime.completed_tasks import completed_task_times
from app.runtime.continuous_occupation import unfinished_members
from app.runtime.event_validation import EventConflict
from app.runtime.execution_resources import acquire, group_uses, release
from app.runtime.material_ledger import apply_material_effects
from app.runtime.replan_boundary import is_clock_continuation
from app.runtime.stage_progress import resource_projection, update_stage_spans


def binding_for(session: RuntimeSession, payload: ExecutionPayload) -> ExecutionBinding:
    binding = next((b for b in session.bindings if payload.task_id in b.assignment.task_ids), None)
    if binding is None:
        raise ValueError("工序没有已发布的执行预约")
    return binding


def apply_execution(
    session: RuntimeSession,
    event: RuntimeEvent,
    dependencies: tuple[tuple[str, str, int, int | None], ...],
    minimum_durations: tuple[tuple[str, int], ...] = (),
    lot_sources: dict[str, MaterialSpec] | None = None,
    devices: tuple[DeviceInstance, ...] = (),
) -> RuntimeSession:
    payload = event.payload
    assert isinstance(payload, ExecutionPayload)
    state, details = session.runtime, session.runtime.details
    assert details is not None
    records = {e.execution_id: e for e in state.executions}
    previous = records.get(payload.execution_id)
    binding = binding_for(session, payload)
    if binding.carrier.kind == "INVENTORY_SUPPLY":
        raise ValueError("库存满足不能冒充实际加工反馈")
    at = state.time_origin.offset(event.occurred_at)
    source_span = next(p.interval for p in binding.task_spans if p.task_id == payload.task_id)
    group = tuple(p.task_id for p in binding.task_spans if p.interval == source_span)
    occupations = details.occupancies
    blocked = set(details.blocked_task_ids)
    if event.event_type == "OPERATION_STARTED":
        if (
            previous
            and previous.resumption
            and event.occurred_at > previous.resumption.latest_start_at
        ):
            raise ValueError("续做开始已超过审核的最长中断时间")
        pending_retry = previous is not None and previous.status == "PENDING"
        if (
            previous is not None
            and pending_retry
            and (
                previous.previous_execution_id is None
                or set(previous.task_ids) != set(binding.assignment.task_ids)
                or previous.started_at is not None
            )
        ):
            raise EventConflict("重试身份与已批准的完整执行成员不一致")
        if (
            previous
            and not pending_retry
            and (previous.status != "RUNNING" or set(group) & set(previous.started_task_ids))
        ):
            raise EventConflict("执行已经开始或结束")
        if previous is None and any(
            set(e.task_ids) & set(binding.assignment.task_ids)
            for e in records.values()
            if e.status in {"PENDING", "RUNNING", "COMPLETED", "FAILED"}
        ):
            raise EventConflict("载体成员已有实际执行")
        if (
            session.dispatch_blocked
            and (previous is None or pending_retry)
            and not is_clock_continuation(session, payload.task_id)
        ):
            raise ValueError("计划派发已暂停，须先获得有效计划")
        completed = completed_task_times(state)
        current = {t.root for t in group}
        for before, after, low, high in dependencies:
            if after in current and before not in current:
                if (
                    before not in completed
                    or at < completed[before] + low
                    or (high is not None and at > completed[before] + high)
                ):
                    raise ValueError("真实前置未完成或实际等待不满足工艺")
        origin = at - source_span.start_sec + binding.assignment.interval.start_sec
        if previous and previous.started_at:
            origin = state.time_origin.offset(previous.started_at)
        phases = [u for u in binding.assignment.resource_uses]
        phases.extend(
            p.resource_use
            for p in binding.carrier.resource_phases
            if p.start_offset_sec < source_span.end_sec - binding.assignment.interval.start_sec
            and p.end_offset_sec > source_span.start_sec - binding.assignment.interval.start_sec
        )
        uses = group_uses(tuple(phases))
        if payload.resource_ids and set(payload.resource_ids) != {u.resource_id for u in uses}:
            raise ValueError("实际资源绑定与预约不符")
        occupations, transfers = acquire(
            session, event, payload.execution_id, uses, binding, devices
        )
        for owner_id, actual_span in transfers:
            owner = records[owner_id]
            records[owner_id] = owner.model_copy(
                update={
                    "resource_spans": (
                        *owner.resource_spans,
                        *((actual_span,) if actual_span else ()),
                    ),
                    "event_refs": tuple(dict.fromkeys((*owner.event_refs, event.event_id))),
                }
            )
        spans = tuple(
            TaskSpan(
                task_id=p.task_id,
                interval=Interval(
                    start_sec=origin + p.interval.start_sec - binding.assignment.interval.start_sec,
                    end_sec=origin + p.interval.end_sec - binding.assignment.interval.start_sec,
                ),
            )
            for p in binding.task_spans
        )
        scheduled = tuple(
            ActualResourceSpan(
                resource=u,
                interval=Interval(start_sec=origin + lo, end_sec=origin + hi),
                event_refs=(event.event_id.root,),
            )
            for u, lo, hi in [
                *((u, 0, binding.carrier.duration_sec) for u in binding.assignment.resource_uses),
                *(
                    (p.resource_use, p.start_offset_sec, p.end_offset_sec)
                    for p in binding.carrier.resource_phases
                ),
            ]
        )
        if previous is None or pending_retry:
            record = ExecutionRecord(
                execution_id=payload.execution_id,
                task_ids=binding.assignment.task_ids,
                status="RUNNING",
                source=event.source,
                event_refs=(*(previous.event_refs if previous else ()), event.event_id),
                previous_execution_id=previous.previous_execution_id if previous else None,
                recovery_rule_id=previous.recovery_rule_id if previous else None,
                recovery_kind=previous.recovery_kind if previous else None,
                resumption=previous.resumption if previous else None,
                started_at=event.occurred_at,
                resource_ids=tuple(sorted({u.resource_id for u in uses})),
                remaining_sec=binding.carrier.duration_sec,
                remaining_source_ref=event.event_id.root,
                remaining_observed_at=event.occurred_at,
                carrier_id=binding.carrier.carrier_id.root,
                task_spans=spans,
                started_task_ids=group,
                scheduled_resource_spans=scheduled,
            )
        else:
            record = previous.model_copy(
                update={
                    "started_task_ids": (*previous.started_task_ids, *group),
                    "task_spans": update_stage_spans(previous, group, start_sec=at),
                    "event_refs": (*previous.event_refs, event.event_id),
                }
            )
    else:
        if previous is None or payload.task_id not in previous.started_task_ids:
            raise ValueError("反馈缺少对应实际开始")
        if previous.status != "RUNNING" or payload.task_id in previous.completed_task_ids:
            raise EventConflict("迟到反馈不能覆盖已结束事实")
        if previous.started_at is None or event.occurred_at < previous.started_at:
            raise ValueError("反馈早于实际开始")
        minima = dict(minimum_durations)
        if previous.resumption:
            minima.update({t.root: previous.resumption.authorized_duration_sec for t in group})
        if event.event_type in {"OPERATION_COMPLETED", "DURATION_UPDATED"}:
            for span in previous.task_spans:
                if span.task_id not in group or span.task_id.root not in minima:
                    continue
                minimum = (
                    previous.resumption.authorized_duration_sec
                    if previous.resumption
                    else minima[span.task_id.root]
                )
                earliest_finish = span.interval.start_sec + minimum
                proposed_finish = (
                    at + (payload.remaining_sec or 0)
                    if event.event_type == "DURATION_UPDATED"
                    else at
                )
                if proposed_finish < earliest_finish:
                    raise ValueError("反馈不得缩短菜谱必需的等待或固定工艺时间")
        if event.event_type == "DURATION_UPDATED":
            record = previous.model_copy(
                update={
                    "task_spans": update_stage_spans(
                        previous, group, end_sec=at + (payload.remaining_sec or 0)
                    ),
                    "remaining_source_ref": event.event_id.root,
                    "remaining_observed_at": event.occurred_at,
                    "event_refs": (*previous.event_refs, event.event_id),
                }
            )
        else:
            finished = tuple(dict.fromkeys((*previous.completed_task_ids, *group)))
            terminal = event.event_type == "OPERATION_FAILED" or set(finished) == set(
                previous.task_ids
            )
            spans = update_stage_spans(previous, group, end_sec=at)
            occupations, actual = release(
                session,
                event,
                payload.execution_id,
                device_confirmed=payload.resource_release_status == "CONFIRMED",
                terminal=terminal,
                completing=group if event.event_type == "OPERATION_COMPLETED" else (),
            )
            if terminal and payload.resource_release_status == "CONFIRMED":
                for item in details.occupancies:
                    if item.execution_id == payload.execution_id and item.released_at is None:
                        blocked.update(
                            unfinished_members(
                                session,
                                item,
                                group if event.event_type == "OPERATION_COMPLETED" else (),
                            )
                        )
            record = previous.model_copy(
                update={
                    "status": (
                        ExecutionStatus.FAILED
                        if event.event_type == "OPERATION_FAILED"
                        else ExecutionStatus.COMPLETED
                    )
                    if terminal
                    else ExecutionStatus.RUNNING,
                    "finished_at": event.occurred_at if terminal else None,
                    "failure_output_status": payload.output_status
                    if event.event_type == "OPERATION_FAILED"
                    else previous.failure_output_status,
                    "task_spans": spans,
                    "completed_task_ids": finished
                    if event.event_type == "OPERATION_COMPLETED"
                    else previous.completed_task_ids,
                    "resource_spans": (*previous.resource_spans, *actual),
                    "event_refs": (*previous.event_refs, event.event_id),
                }
            )
    if record.status == "RUNNING":
        record = record.model_copy(
            update={
                "remaining_sec": max(span.interval.end_sec for span in record.task_spans) - at,
                "remaining_source_ref": event.event_id.root,
                "remaining_observed_at": event.occurred_at,
                "scheduled_resource_spans": resource_projection(
                    record, binding, state, occupations, event.event_id.root
                ),
            }
        )
    changed = apply_material_effects(event, session, previous, lot_sources)
    consumed = {m.lot_id: m for m in previous.consumed} if previous else {}
    produced = {m.lot_id: m for m in previous.produced} if previous else {}
    consumed.update({m.lot_id: m for m in payload.consumed})
    produced.update({m.lot_id: m for m in payload.produced})
    record = record.model_copy(
        update={"consumed": tuple(consumed.values()), "produced": tuple(produced.values())}
    )
    records[record.execution_id] = record
    assert changed.runtime.details is not None
    return changed.model_copy(
        update={
            "runtime": changed.runtime.model_copy(
                update={
                    "executions": tuple(records.values()),
                    "details": changed.runtime.details.model_copy(
                        update={
                            "occupancies": occupations,
                            "blocked_task_ids": tuple(sorted(blocked, key=lambda task: task.root)),
                        }
                    ),
                }
            )
        }
    )
