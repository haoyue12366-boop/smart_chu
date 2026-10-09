"""根据已发生事件准备不可变重排快照，不推断未确认的完成。"""

from app.domain.candidates import stable_id
from app.domain.events import EventApplyResult
from app.domain.ids import TaskId
from app.domain.knowledge import MenuKnowledgeView
from app.domain.policy import SchedulingPolicy
from app.domain.ports import ReplanRequest
from app.domain.runtime_session import RuntimeSession
from app.domain.runtime_snapshot import RuntimeSnapshot
from app.domain.time import Interval
from app.runtime.material_reservations import release_future
from app.runtime.running_inputs import check_running_inputs


def project_runtime(session: RuntimeSession, knowledge: MenuKnowledgeView) -> RuntimeSnapshot:
    # 仅投影本次可重新使用的未开始预约；实际撤销仍在成功发布事务中落账。
    session = release_future(
        session,
        "projection",
        session.runtime.event_refs[-1].root if session.runtime.event_refs else "projection",
    )
    check_running_inputs(session)
    state = session.runtime
    details = state.details
    assert details is not None
    records = []
    for execution in state.executions:
        if execution.status == "RUNNING":
            if execution.interruption_event_refs:
                raise ValueError("执行受设备异常影响，须确认实际结果或采用已批准恢复路径")
            if execution.remaining_sec is None or execution.remaining_observed_at is None:
                raise ValueError("运行中工序缺少剩余时间观测")
            active = set(execution.started_task_ids) - set(execution.completed_task_ids)
            for stage_span in execution.task_spans:
                if (
                    stage_span.task_id in active
                    and stage_span.interval.end_sec <= state.now_offset_sec
                ):
                    raise ValueError("当前运行阶段已到预计结束，须确认完成或更新阶段剩余时长")
                if (
                    stage_span.task_id not in execution.started_task_ids
                    and stage_span.interval.start_sec < state.now_offset_sec
                ):
                    raise ValueError("内部阶段尚未确认实际开始，不能按过去的计划时间冻结")
            elapsed = state.now_offset_sec - state.time_origin.offset(
                execution.remaining_observed_at
            )
            remaining = execution.remaining_sec - elapsed
            if remaining <= 0:
                raise ValueError("运行已超过预计结束，须确认完成或更新剩余时间")
            spans = execution.task_spans
            resources = execution.scheduled_resource_spans
            if len({p.interval for p in spans}) == 1 and spans:
                span = spans[0].interval
                spans = tuple(
                    p.model_copy(
                        update={
                            "interval": Interval(
                                start_sec=span.start_sec, end_sec=state.now_offset_sec + remaining
                            )
                        }
                    )
                    for p in spans
                )
                resources = tuple(
                    p.model_copy(
                        update={
                            "interval": Interval(
                                start_sec=p.interval.start_sec,
                                end_sec=state.now_offset_sec + remaining,
                            )
                        }
                    )
                    for p in resources
                )
            execution = execution.model_copy(
                update={
                    "remaining_sec": remaining,
                    "task_spans": spans,
                    "scheduled_resource_spans": resources,
                }
            )
        records.append(execution)
    recipes = {r.recipe_id: r for r in knowledge.recipes}
    retired = tuple(
        TaskId(stable_id("task", i.recipe_instance_id.root, o.operation_id.root))
        for i in session.menu
        if i.recipe_instance_id.root in details.cancelled_instance_ids
        for o in recipes[i.recipe_id].operations
    )
    return state.model_copy(
        update={
            "executions": tuple(records),
            "details": details.model_copy(
                update={"retired_task_ids": retired, "inventory_rules": session.inventory_rules}
            ),
        }
    )


def prepare_replan(
    session_snapshot: RuntimeSession,
    event_result: EventApplyResult | None,
    knowledge: MenuKnowledgeView,
    policy: SchedulingPolicy,
) -> ReplanRequest:
    runtime = project_runtime(session_snapshot, knowledge)
    assert runtime.details is not None
    menu = tuple(
        i
        for i in session_snapshot.menu
        if i.recipe_instance_id.root not in runtime.details.cancelled_instance_ids
    )
    return ReplanRequest(
        request_id=(
            event_result.event_id.root
            if event_result
            else f"replan-{runtime.session_id.root}-{runtime.state_revision}"
        ),
        menu=menu,
        policy=policy,
        runtime=runtime,
    )
