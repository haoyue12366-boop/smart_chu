"""已提交完成事件对应的菜品与工序提示，与执行账本共用事务。"""

from sqlalchemy import Connection

from app.domain.candidates import stable_id
from app.domain.events import ExecutionPayload, RuntimeEvent
from app.domain.runtime_snapshot import ExecutionRecord, RuntimeSnapshot
from app.storage.notification_stream import append_message
from app.storage.repositories import RuntimeRepository


def record_completion(
    tx: Connection, event: RuntimeEvent, runtime: RuntimeSnapshot, execution: ExecutionRecord
) -> None:
    if not isinstance(event.payload, ExecutionPayload) or event.event_type != "OPERATION_COMPLETED":
        return
    target = next((s for s in execution.task_spans if s.task_id == event.payload.task_id), None)
    if target is None:
        return
    completed = tuple(
        span.task_id
        for span in execution.task_spans
        if span.interval == target.interval and span.task_id in execution.completed_task_ids
    )
    if not completed:
        return
    sid = runtime.session_id.root
    repo = RuntimeRepository(tx)
    session = repo.get(sid)
    binding = next(
        (b for b in session.bindings if event.payload.task_id in b.assignment.task_ids), None
    )
    version = binding.plan_version if binding else runtime.current_plan_version
    if repo.plan(sid, version) is None:
        return
    problem = repo.problem(sid, version)
    names = {recipe.recipe_instance_id: recipe.name for recipe in problem.recipe_instances}
    operations = {task.task_id: task for task in problem.logical_tasks}
    source_label = {
        "SCHEDULE_CLOCK": "计划时钟推算",
        "SIMULATED": "模拟执行",
        "MANUAL_CONFIRM": "人工确认",
        "DEVICE_FEEDBACK": "设备反馈",
    }.get(event.source, str(event.source))
    labels = tuple(
        f"{names[operations[task].recipe_instance_id]}："
        f"{operations[task].operation.description or operations[task].operation.action.value}已完成"
        for task in completed
        if task in operations
    )
    identity = stable_id(
        "operation-completed", sid, execution.execution_id.root, *sorted(t.root for t in completed)
    )
    append_message(
        tx,
        sid,
        identity,
        {
            "notification_id": identity,
            "event_id": event.event_id.root,
            "plan_version": version,
            "kind": "OPERATION_COMPLETED",
            "text": "；".join(labels) + f"（{source_label}）",
            "data": {
                "task_ids": [task.root for task in completed],
                "execution_id": execution.execution_id.root,
                "source": event.source.value,
                "completed_at": event.occurred_at.isoformat(),
                "completed_offset_sec": runtime.time_origin.offset(event.occurred_at),
                "state_revision": runtime.state_revision,
            },
        },
    )
