"""记录即时重排请求及必要衔接；已开始载体继续执行，不设置全局等待。"""

from app.domain.ids import TaskId
from app.domain.runtime_session import RuntimeSession
from app.domain.scheduling_problem import SchedulingProblem


def earliest_replan_sec(session: RuntimeSession, requested_sec: int) -> int:
    # 同一载体的中间阶段不能单独冻结：保留其全部阶段到自然完成。
    return max(
        [requested_sec, session.runtime.now_offset_sec]
        + [
            span.interval.end_sec
            for record in session.runtime.executions
            if record.status == "RUNNING"
            for span in record.task_spans
            if span.task_id not in record.completed_task_ids
        ]
    )


def _continuations(
    session: RuntimeSession, problem: SchedulingProblem, boundary: int
) -> tuple[int, tuple[TaskId, ...]]:
    """计算期间不能切断已有最大间隔约束；保留已发布计划的必要延续。"""
    ports = {s.task_id: s.interval for b in session.bindings for s in b.task_spans}
    ports.update({s.task_id: s.interval for e in session.runtime.executions for s in e.task_spans})
    started = {
        t
        for e in session.runtime.executions
        if e.status in {"RUNNING", "COMPLETED"}
        for t in e.started_task_ids
    }
    done = {t for e in session.runtime.executions for t in e.completed_task_ids}
    clock = session.schedule_clock
    keep = set(clock.replan_continuation_task_ids if clock else ()) - done
    bindings = {
        t: b
        for b in session.bindings
        for t in b.assignment.task_ids
        if b.plan_version == session.runtime.current_plan_version
    }
    while True:
        needed = set(keep)
        roots = started | keep
        for edge in problem.dependencies:
            if (
                edge.predecessor_id in roots
                and edge.predecessor_id in ports
                and edge.max_lag_sec is not None
            ):
                needed.add(edge.successor_id)
            if edge.successor_id in keep and edge.predecessor_id not in started:
                needed.add(edge.predecessor_id)
        for relation in problem.mandatory_programs.time_relations:
            if (
                relation.left_task in roots
                and relation.left_task in ports
                and relation.max_offset_sec is not None
            ):
                needed.add(relation.right_task)
        needed -= done
        # 原绑定是可执行载体；必要成员不能拆开共享/多阶段操作。
        for task in tuple(needed):
            if task in bindings:
                needed.update(bindings[task].assignment.task_ids)
        needed = {t for t in needed - done if t in bindings}
        extended = max([boundary] + [ports[t].end_sec for t in needed])
        if needed == keep and extended == boundary:
            return boundary, tuple(sorted(keep, key=lambda task: task.root))
        keep, boundary = needed, extended


def is_clock_continuation(session: RuntimeSession, task: TaskId) -> bool:
    clock = session.schedule_clock
    return bool(
        clock
        and session.requires_replan
        and not session.last_planning_failure
        and task in clock.replan_continuation_task_ids
    )


def defer_replan(
    session: RuntimeSession, requested_sec: int | None, problem: SchedulingProblem | None = None
) -> RuntimeSession:
    clock = session.schedule_clock
    if clock is None or not session.requires_replan:
        return session
    requested = clock.replan_requested_sec
    if requested is None:
        requested = requested_sec if requested_sec is not None else session.runtime.now_offset_sec
    boundary = earliest_replan_sec(session, requested)
    continuation: tuple[TaskId, ...] = ()
    if problem is not None:
        boundary, continuation = _continuations(session, problem, boundary)
    return session.model_copy(
        update={
            "schedule_clock": clock.model_copy(
                update={
                    "replan_requested_sec": requested,
                    "replan_not_before_sec": None,
                    "replan_continuation_task_ids": continuation,
                }
            )
        }
    )
