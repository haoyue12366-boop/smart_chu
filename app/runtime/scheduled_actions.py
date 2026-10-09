"""依据当前发布绑定生成下一动作；模拟和排程时钟共用资源、阶段与物料规则。"""

from dataclasses import dataclass

from app.domain.candidates import stable_id
from app.domain.events import ExecutionPayload
from app.domain.ids import TaskId
from app.domain.runtime_session import ExecutionBinding, RuntimeSession
from app.domain.scheduling_problem import SchedulingProblem
from app.runtime.completed_tasks import completed_task_times
from app.runtime.dispatch_guard import guard_start, stage_uses
from app.runtime.dispatch_windows import ObservedDependency, observed_window
from app.runtime.execution_resources import acquire
from app.runtime.feedback import FeedbackAdapter
from app.runtime.feedback_template import proposed_payload, release_eligible
from app.runtime.replan_boundary import is_clock_continuation
from app.runtime.service import RuntimeService
from app.storage.repositories import RuntimeRepository


@dataclass(frozen=True)
class PlannedAction:
    at: int
    priority: int
    identity: str
    kind: str
    payload: dict[str, object]
    binding: ExecutionBinding | None = None
    group: tuple[TaskId, ...] = ()


class PublishedActions:
    def __init__(
        self, runtime: RuntimeService, session_id: str, *, namespace: str = "sim", seed: int = 0
    ) -> None:
        self.runtime, self.session_id = runtime, session_id
        self.namespace, self.seed = namespace, seed
        self._guard_problem: SchedulingProblem | None = None
        self._guard_plan_version = -1

    def planned(self, session: RuntimeSession, now_offset_sec: int) -> tuple[PlannedAction, ...]:
        actions = []
        guarded = self.namespace == "clock" or session.policy.dispatch_guard_policy_id != "NONE"
        problem = None
        if guarded and session.bindings:
            if self._guard_plan_version != session.runtime.current_plan_version:
                with self.runtime.store.engine.connect() as tx:
                    self._guard_problem = RuntimeRepository(tx).problem(
                        self.session_id, session.runtime.current_plan_version
                    )
                self._guard_plan_version = session.runtime.current_plan_version
            problem = self._guard_problem
            assert problem is not None
        for binding in session.bindings:
            if binding.carrier.kind == "INVENTORY_SUPPLY":
                continue
            record = next(
                (
                    e
                    for e in session.runtime.executions
                    if (e.carrier_id == binding.carrier.carrier_id.root and e.status == "RUNNING")
                    or (
                        set(e.task_ids) == set(binding.assignment.task_ids)
                        and e.status == "PENDING"
                    )
                ),
                None,
            )
            if record is None:
                record = next(
                    (
                        e
                        for e in reversed(session.runtime.executions)
                        if e.carrier_id == binding.carrier.carrier_id.root
                    ),
                    None,
                )
            if record and record.status in {"COMPLETED", "FAILED", "CANCELLED"}:
                continue
            if record and record.interruption_event_refs:
                continue
            details = session.runtime.details
            if record and details and set(record.task_ids).intersection(details.blocked_task_ids):
                continue
            if record is None and (
                (
                    session.dispatch_blocked
                    and not any(
                        is_clock_continuation(session, task) for task in binding.assignment.task_ids
                    )
                )
                or binding.plan_version != session.runtime.current_plan_version
            ):
                continue
            groups = {
                p.interval: tuple(s.task_id for s in binding.task_spans if s.interval == p.interval)
                for p in binding.task_spans
            }
            for span, group in groups.items():
                if record and set(group) <= set(record.completed_task_ids):
                    continue
                started = record is not None and set(group) <= set(record.started_task_ids)
                if not started and session.dispatch_blocked and session.last_planning_failure:
                    continue
                if (
                    session.dispatch_blocked
                    and not started
                    and (record is None or record.status != "RUNNING")
                    and not any(is_clock_continuation(session, task) for task in group)
                ):
                    continue
                execution_id = (
                    record.execution_id.root
                    if record
                    else stable_id(
                        self.namespace + "-execution",
                        self.session_id,
                        str(self.seed),
                        binding.carrier.carrier_id.root,
                    )
                )
                kind = "OPERATION_COMPLETED" if started else "OPERATION_STARTED"
                actual_span = (
                    next((p.interval for p in record.task_spans if p.task_id == group[0]), span)
                    if record
                    else span
                )
                offset = actual_span.end_sec if started else actual_span.start_sec
                at = max(now_offset_sec, offset)
                payload: dict[str, object] = {"execution_id": execution_id, "task_id": group[0]}
                if not started and problem is not None:
                    current = {t.root for t in group}
                    completed = completed_task_times(session.runtime)
                    dependencies = tuple(
                        (d.predecessor_id.root, d.successor_id.root, d.min_lag_sec, d.max_lag_sec)
                        for d in problem.dependencies
                    )
                    observed = tuple(
                        ObservedDependency(before, after, completed.get(before), 0, low, high)
                        for before, after, low, high in dependencies
                        if after in current and before not in current
                    )
                    details = session.runtime.details
                    assert details is not None
                    starts = dict(details.earliest_starts)
                    instances = {
                        t.task_id.root: t.recipe_instance_id.root for t in problem.logical_tasks
                    }
                    buffers = {
                        t.root: b.not_before_sec
                        for b in problem.duration_buffers
                        for t in b.root_task_ids
                    }
                    not_before = max(
                        problem.runtime.now_offset_sec,
                        *(starts.get(instances.get(t.root, ""), 0) for t in group),
                        *(buffers.get(t.root, 0) for t in group),
                    )
                    window = observed_window(now_offset_sec, not_before, at, observed)
                    if window.code in {"ACTUAL_WINDOW_EXPIRED", "START_WINDOW_EMPTY"}:
                        raise ValueError("实际启动窗口已关闭；须记录失败并评估恢复路径")
                    if window.dispatch_at_sec is None:
                        continue
                    at = window.dispatch_at_sec
                    guard = guard_start(session, self.runtime.knowledge, dependencies, group, at)
                    if guard.blocked:
                        continue
                    probe = FeedbackAdapter().event(
                        session,
                        "dispatch-guard-probe",
                        kind,
                        payload,
                        session.runtime.time_origin.at(at),
                    )
                    assert isinstance(probe.payload, ExecutionPayload)
                    try:
                        acquire(
                            session,
                            probe,
                            probe.payload.execution_id,
                            stage_uses(binding, group[0]),
                            binding,
                            self.runtime.knowledge.devices,
                        )
                    except ValueError:
                        continue
                identity = stable_id(
                    self.namespace + "-event",
                    self.session_id,
                    str(self.seed),
                    execution_id,
                    group[0].root,
                    kind,
                )
                actions.append(
                    PlannedAction(at, 0 if started else 2, identity, kind, payload, binding, group)
                )
        return tuple(actions)

    def payload(
        self,
        session: RuntimeSession,
        binding: ExecutionBinding,
        group: tuple[TaskId, ...],
        execution_id: str,
        *,
        completed: bool,
    ) -> dict[str, object]:
        with self.runtime.store.engine.connect() as tx:
            problem = RuntimeRepository(tx).problem(self.session_id, binding.plan_version)
        result = proposed_payload(
            session,
            problem,
            group,
            execution_id,
            completed=completed,
            lot_namespace=self.namespace + "-lot",
        )
        result["output_status"] = "QUALIFIED" if completed else "UNKNOWN"
        result["resource_release_status"] = (
            "CONFIRMED"
            if completed and release_eligible(session, execution_id, group)
            else "UNCONFIRMED"
        )
        return result
