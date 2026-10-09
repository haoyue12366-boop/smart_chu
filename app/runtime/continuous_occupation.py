"""连续工艺的真实持有与分段占用证据；确认清空不能冒充正常阶段交接。"""

from app.domain.events import RuntimeEvent
from app.domain.ids import TaskId
from app.domain.runtime_facts import ActualResourceSpan, Occupancy
from app.domain.runtime_session import RuntimeSession
from app.domain.time import Interval


def unfinished_members(
    session: RuntimeSession, item: Occupancy, completing: tuple[TaskId, ...] = ()
) -> tuple[TaskId, ...]:
    completed = set(completing)
    for record in session.runtime.executions:
        completed.update(
            record.task_ids if record.status == "COMPLETED" else record.completed_task_ids
        )
    return tuple(task for task in item.reservation_members if task not in completed)


def observed_span(
    session: RuntimeSession, item: Occupancy, event: RuntimeEvent
) -> ActualResourceSpan | None:
    """只追加尚未记录的实际持有区间，不重写已结束工序或重复记账。"""
    origin = session.runtime.time_origin
    record = next(e for e in session.runtime.executions if e.execution_id == item.execution_id)
    start = max(
        [origin.offset(item.started_at)]
        + [
            span.interval.end_sec
            for span in record.resource_spans
            if span.resource == item.resource
        ]
    )
    end = origin.offset(event.occurred_at)
    if end < start:
        raise ValueError("占用观测早于已经记录的实际区间")
    if end == start:
        return None
    return ActualResourceSpan(
        resource=item.resource,
        interval=Interval(start_sec=start, end_sec=end),
        event_refs=(event.event_id.root,),
    )
