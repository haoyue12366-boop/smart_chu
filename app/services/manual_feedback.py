"""人工反馈只读准备；用户明确确认和编辑后仍提交统一版本化事件。"""

from app.domain.candidates import stable_id
from app.domain.errors import ServiceError
from app.domain.events import ExecutionPayload
from app.domain.ids import TaskId
from app.runtime.feedback_template import proposed_payload, release_eligible
from app.runtime.material_reports import source_specs
from app.services.container import ServiceContainer
from app.storage.repositories import RuntimeRepository


def prepare_feedback(
    services: ServiceContainer, session_id: str, task_id: str, *, completed: bool
) -> dict[str, object]:
    runtime, _ = services.for_session(session_id)
    session = runtime.get(session_id)
    if session.runtime.execution_mode not in {"MANUAL_CONFIRM", "SCHEDULE_CLOCK"}:
        raise ServiceError("INVALID_REQUEST", "人工反馈准备只用于人工确认或排程时钟模式")
    if not completed and session.dispatch_blocked:
        raise ServiceError("STATE_CONFLICT", "操作已暂停，请先处理异常或等待有效重排")
    task = TaskId(task_id)
    binding = next((b for b in session.bindings if task in b.assignment.task_ids), None)
    if binding is None:
        raise ServiceError("UNKNOWN_TASK", "当前会话没有该操作")
    span = next(s.interval for s in binding.task_spans if s.task_id == task)
    group = tuple(s.task_id for s in binding.task_spans if s.interval == span)
    record = next((e for e in reversed(session.runtime.executions) if task in e.task_ids), None)
    if record is not None and record.status in {"COMPLETED", "FAILED", "CANCELLED"}:
        raise ServiceError("STATE_CONFLICT", "该执行已经结束；请查看历史或审核过的恢复操作")
    if record is None and binding.plan_version != session.runtime.current_plan_version:
        raise ServiceError("STATE_CONFLICT", "该操作属于旧计划")
    started = bool(record and set(group) <= set(record.started_task_ids))
    if completed != started:
        raise ServiceError("STATE_CONFLICT", "完成确认要求已经开始，开始确认要求尚未开始")
    execution_id = (
        record.execution_id.root
        if record
        else stable_id("manual-execution", session_id, binding.carrier.carrier_id.root)
    )
    with services.store.engine.connect() as tx:
        problem = RuntimeRepository(tx).problem(session_id, binding.plan_version)
    payload = ExecutionPayload.model_validate(
        proposed_payload(
            session, problem, group, execution_id, completed=completed, lot_namespace="manual-lot"
        )
    )
    return {
        "requires_confirmation": True,
        "notice": "草稿数量来自已发布配方，请按实际投入与产出核对或修改后确认。",
        "source": "MANUAL_CONFIRM",
        "state_revision": session.runtime.state_revision,
        "plan_version": session.runtime.current_plan_version,
        "completed": completed,
        "task_ids": [t.root for t in group],
        "payload": payload.model_dump(mode="json"),
        "movement_names": {
            key: spec.name for key, spec in source_specs(session, runtime.knowledge).items()
        },
        "release_eligible": completed and release_eligible(session, execution_id, group),
    }
