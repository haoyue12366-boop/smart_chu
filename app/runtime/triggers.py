"""重排只响应改变余下计划的事实，重复事件由事务入口提前返回。"""

from app.domain.events import DevicePayload, ExecutionPayload, RuntimeEvent
from app.domain.knowledge import MenuKnowledgeView
from app.domain.runtime_session import RuntimeSession
from app.runtime.device_triggers import device_requires_replan


def replan_reasons(
    before: RuntimeSession,
    after: RuntimeSession,
    event: RuntimeEvent,
    knowledge: MenuKnowledgeView,
) -> tuple[str, ...]:
    kind = event.event_type
    if (
        before.runtime.details
        and after.runtime.details
        and (
            set(after.runtime.details.blocked_task_ids)
            - set(before.runtime.details.blocked_task_ids)
        )
    ):
        return ("CONTINUOUS_PROCESS_INTERRUPTED",)
    if kind == "RESET_SESSION":
        return ()
    if kind == "ADVANCE_SIMULATION":
        return ()
    if kind == "REPLAN_REQUESTED":
        return (kind.value,)
    if kind in {"START_SESSION", "ADD_RECIPE", "CANCEL_RECIPE", "DELAY_RECIPE", "MANUAL_OVERRIDE"}:
        return (kind.value,) if after != before else ()
    if kind in {
        "MATERIAL_ADJUSTED",
        "MATERIAL_SHORTAGE",
        "OPERATION_FAILED",
        "OPERATION_RETRY_REQUESTED",
    }:
        return (kind.value,) if after != before else ()
    payload = event.payload
    if isinstance(payload, DevicePayload):
        return (
            (kind.value,)
            if device_requires_replan(before, after, payload.device_id, knowledge)
            else ()
        )
    if isinstance(payload, ExecutionPayload):
        binding = next(
            (b for b in before.bindings if payload.task_id in b.assignment.task_ids), None
        )
        if binding is None:
            return ("MISSING_PLAN_BINDING",)
        if kind == "DURATION_UPDATED":
            execution = next(
                (e for e in before.runtime.executions if e.execution_id == payload.execution_id),
                None,
            )
            if (
                execution
                and execution.remaining_observed_at
                and execution.remaining_sec is not None
            ):
                span = next(
                    p.interval for p in execution.task_spans if p.task_id == payload.task_id
                )
                remaining = span.end_sec - before.runtime.time_origin.offset(event.occurred_at)
                return ("REMAINING_DURATION_CHANGED",) if remaining != payload.remaining_sec else ()
            return ("REMAINING_DURATION_CHANGED",)
        execution = next(
            (e for e in before.runtime.executions if e.execution_id == payload.execution_id), None
        )
        spans = execution.task_spans if execution and execution.task_spans else binding.task_spans
        span = next(p.interval for p in spans if p.task_id == payload.task_id)
        expected = span.start_sec if kind == "OPERATION_STARTED" else span.end_sec
        if before.runtime.time_origin.offset(event.occurred_at) != expected:
            return ("ACTUAL_TIME_DEVIATION",)
        if payload.produced and payload.output_status != "QUALIFIED":
            return ("OUTPUT_NOT_QUALIFIED",)
    return ()
