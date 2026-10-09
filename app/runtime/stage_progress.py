"""实际阶段边界和仍未开始的阶段预测分开更新，已完成时间保持不变。"""

from app.domain.ids import TaskId
from app.domain.runtime_facts import ActualResourceSpan, Occupancy, TaskSpan
from app.domain.runtime_session import ExecutionBinding
from app.domain.runtime_snapshot import ExecutionRecord, RuntimeSnapshot
from app.domain.time import Interval


def update_stage_spans(
    record: ExecutionRecord,
    group: tuple[TaskId, ...],
    *,
    start_sec: int | None = None,
    end_sec: int | None = None,
) -> tuple[TaskSpan, ...]:
    current = next(span.interval for span in record.task_spans if span.task_id in group)
    start = current.start_sec if start_sec is None else start_sec
    end = start + current.end_sec - current.start_sec if end_sec is None else end_sec
    delta = end - current.end_sec
    result = []
    for span in record.task_spans:
        if span.task_id in group:
            interval = Interval(start_sec=start, end_sec=end)
        elif (
            span.task_id not in record.started_task_ids
            and span.interval.start_sec >= current.end_sec
        ):
            interval = Interval(
                start_sec=span.interval.start_sec + delta,
                end_sec=span.interval.end_sec + delta,
            )
        else:
            interval = span.interval
        result.append(span.model_copy(update={"interval": interval}))
    return tuple(result)


def resource_projection(
    record: ExecutionRecord,
    binding: ExecutionBinding,
    runtime: RuntimeSnapshot,
    occupations: tuple[Occupancy, ...],
    event_ref: str,
) -> tuple[ActualResourceSpan, ...]:
    """真实主动段只计一次；设备全程占用和未来主动阶段均保留。"""
    assert record.started_at is not None
    start = runtime.time_origin.offset(record.started_at)
    end = max(span.interval.end_sec for span in record.task_spans)
    whole_keys = {
        (
            use.physical_resource_id or use.resource_id,
            use.component_id or use.resource_id,
            use.effective_layer_indices,
        )
        for use in binding.assignment.resource_uses
    }
    spans = [
        ActualResourceSpan(
            resource=use,
            interval=Interval(start_sec=start, end_sec=end),
            event_refs=(event_ref,),
        )
        for use in binding.assignment.resource_uses
    ]
    spans.extend(
        span
        for span in record.resource_spans
        if (
            span.resource.physical_resource_id or span.resource.resource_id,
            span.resource.component_id or span.resource.resource_id,
            span.resource.effective_layer_indices,
        )
        not in whole_keys
    )
    source = {span.task_id: span.interval for span in binding.task_spans}
    actual = {span.task_id: span.interval for span in record.task_spans}
    for phase in binding.carrier.resource_phases:
        if phase.task_id in record.started_task_ids:
            continue
        delta = actual[phase.task_id].start_sec - source[phase.task_id].start_sec
        spans.append(
            ActualResourceSpan(
                resource=phase.resource_use,
                interval=Interval(
                    start_sec=binding.assignment.interval.start_sec
                    + phase.start_offset_sec
                    + delta,
                    end_sec=binding.assignment.interval.start_sec + phase.end_offset_sec + delta,
                ),
                event_refs=(event_ref,),
            )
        )
    active = set(record.started_task_ids) - set(record.completed_task_ids)
    active_end = max(
        (span.interval.end_sec for span in record.task_spans if span.task_id in active),
        default=end,
    )
    for occupied in occupations:
        use = occupied.resource
        if (
            occupied.execution_id != record.execution_id
            or occupied.released_at is not None
            or (
                use.physical_resource_id or use.resource_id,
                use.component_id or use.resource_id,
                use.effective_layer_indices,
            )
            in whole_keys
        ):
            continue
        spans.append(
            ActualResourceSpan(
                resource=use,
                interval=Interval(
                    start_sec=runtime.time_origin.offset(occupied.started_at),
                    end_sec=active_end,
                ),
                event_refs=(event_ref,),
            )
        )
    return tuple(spans)
