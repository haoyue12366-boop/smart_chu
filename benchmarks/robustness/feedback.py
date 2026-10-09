"""持续事件反馈实验；到预计结束仍未完成时仅提交显式合成估计。"""

import hashlib
import time
from typing import Protocol, cast

from app.domain.base import content_hash
from app.domain.canonical_recipe import OperationTemplate
from app.domain.events import RuntimeEvent
from app.domain.knowledge import MenuKnowledgeView
from app.domain.runtime_session import ExecutionBinding, RuntimeSession
from app.domain.schedule import CandidateSchedule
from app.domain.scheduling_problem import SchedulingProblem
from app.runtime.triggers import replan_reasons
from app.scheduling.duration_policy import estimated_phase
from benchmarks.robustness.dispatch import DispatchFailure
from benchmarks.robustness.observed_session import apply_observation, event
from benchmarks.robustness.simulator import ObservationCache, ReadyStage, TrajectorySimulator


class DurationSource(Protocol):
    def duration(
        self, nominal: int, operations: tuple[tuple[str, OperationTemplate], ...]
    ) -> tuple[int, bool]: ...


class ObservedReplanner(Protocol):
    def __call__(
        self, knowledge: MenuKnowledgeView, observed: RuntimeSession
    ) -> tuple[RuntimeSession | None, SchedulingProblem | None, dict[str, object]]: ...


class ContinuousTrajectorySimulator(TrajectorySimulator):
    """旧单次实验仍使用原类；本类使用生产触发规则且不限制重排次数。"""

    def __init__(
        self,
        knowledge: MenuKnowledgeView,
        initial: RuntimeSession,
        problem: SchedulingProblem,
        candidate: CandidateSchedule,
        future: DurationSource,
        config: dict[str, object],
        planner: ObservedReplanner | None = None,
        cache: ObservationCache | None = None,
    ) -> None:
        super().__init__(knowledge, initial, problem, candidate, future, config, planner, cache)
        self.feedback_triggers: list[dict[str, object]] = []
        self._pending_reasons: set[str] = set()

    def integer_setting(self, name: str, default: int) -> int:
        value = self.config.get(name, default)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError("反馈设置须为正整数：" + name)
        return value

    def register_trigger(self, before: RuntimeSession, request: RuntimeEvent) -> None:
        reasons = replan_reasons(before, self.session, request, self.knowledge)
        if reasons:
            self.feedback_triggers.append(
                {
                    "event_id": request.event_id.root,
                    "event_type": request.event_type.value,
                    "at_sec": self.session.runtime.now_offset_sec,
                    "state_revision": self.session.runtime.state_revision,
                    "reasons": list(reasons),
                }
            )
            self._pending_reasons.update(reasons)

    def observe(self, kind: str, stage: ReadyStage, at: int) -> None:
        before = self.session
        super().observe(kind, stage, at)
        self.register_trigger(before, RuntimeEvent.model_validate(self.events[-1]))

    def apply_feedback(self, request: RuntimeEvent) -> bool:
        for previous in self.events:
            if previous["event_id"] == request.event_id.root:
                if RuntimeEvent.model_validate(previous) != request:
                    raise ValueError("同一反馈身份不能绑定不同内容")
                return False
        before = self.session
        changed = apply_observation(before, request, self.knowledge, self.dependencies, self.minima)
        self.session = changed
        self.prefix = hashlib.sha256((self.prefix + request.model_dump_json()).encode()).hexdigest()
        self.events.append(request.model_dump(mode="json"))
        self.register_trigger(before, request)
        return True

    def next_feedback(self) -> tuple[int, str, str] | None:
        timers = []
        for record in self.session.runtime.executions:
            if record.status != "RUNNING":
                continue
            active = set(record.started_task_ids) - set(record.completed_task_ids)
            for span in record.task_spans:
                if (
                    span.task_id in active
                    and span.task_id not in self.fixed_program_tasks
                    and estimated_phase(self.operations[span.task_id][1]) != "FIXED_PROCESS"
                ):
                    timers.append(
                        (span.interval.end_sec, span.task_id.root, record.execution_id.root)
                    )
        return min(timers, default=None)

    def update_remaining(self, timer: tuple[int, str, str]) -> None:
        at, task_id, execution_id = timer
        # 只观察“此刻仍在执行”；30 秒是预先声明的合成估计，不读取 _finishes。
        extension = self.integer_setting("feedback_extension_sec", 30)
        request = event(
            self.session,
            f"remaining-{execution_id}-{task_id}-{at}",
            "DURATION_UPDATED",
            {"task_id": task_id, "execution_id": execution_id, "remaining_sec": extension},
            at,
        )
        self.apply_feedback(request)

    def flush_replan(self) -> None:
        if not self._pending_reasons:
            return
        reasons = sorted(self._pending_reasons)
        self._pending_reasons.clear()
        if self.planner is None or len(self.completed()) == len(self.operations):
            return
        snapshot = self.session
        updated, problem, measurement = self.planner(self.knowledge, snapshot)
        self.replans.append({**measurement, "trigger_reasons": reasons})
        if updated is None or problem is None:
            self.session = snapshot.model_copy(update={"dispatch_blocked": True})
            raise DispatchFailure(
                "持续反馈重排失败：" + str(measurement.get("failure")),
                {
                    "code": "REPLAN_FAILED",
                    "now_sec": snapshot.runtime.now_offset_sec,
                    "state_revision": snapshot.runtime.state_revision,
                    "reasons": reasons,
                    "measurement": measurement,
                },
            )
        if (
            content_hash(self.session) != content_hash(snapshot)
            or updated.runtime.state_revision != snapshot.runtime.state_revision
            or updated.runtime.current_plan_version != snapshot.runtime.current_plan_version + 1
            or updated.runtime.knowledge_version != snapshot.runtime.knowledge_version
            or updated.runtime.snapshot_id != snapshot.runtime.snapshot_id
        ):
            raise DispatchFailure(
                "持续反馈计算结果已过期，拒绝发布",
                {"code": "STALE_REPLAN_RESULT", "state_revision": snapshot.runtime.state_revision},
            )
        self.session, self.problem = updated, problem
        self.prefix = hashlib.sha256(
            (content_hash(updated) + ":" + problem.problem_hash).encode()
        ).hexdigest()

    def start_stage(self, stage: ReadyStage) -> None:
        binding = cast(ExecutionBinding, stage.binding)
        planned = next(
            p.interval.start_sec for p in binding.task_spans if p.task_id == stage.group[0]
        )
        self.wait_sec += max(0, stage.at - planned)
        self.observe("OPERATION_STARTED", stage, stage.at)
        record = next(
            e for e in self.session.runtime.executions if e.execution_id.root == stage.execution_id
        )
        span = next(p.interval for p in record.task_spans if p.task_id == stage.group[0])
        nominal = span.end_sec - span.start_sec
        if set(stage.group) & self.fixed_program_tasks:
            duration, variable = nominal, False
        else:
            duration, variable = self._future.duration(
                nominal, tuple(self.operations[t] for t in stage.group)
            )
        self.perturbed += variable
        self.fixed += not variable
        self._finishes[stage.group] = stage.at + duration, stage

    def run(self) -> dict[str, object]:
        began = time.perf_counter_ns()
        try:
            for _ in range(self.integer_setting("max_events", 5000)):
                if len(self.completed()) == len(self.operations):
                    # 复用原独立终局扫描和固定工艺检查，不复制 Validator。
                    result = super().run()
                    result["elapsed_ms"] = (time.perf_counter_ns() - began) / 1_000_000
                    return self.with_feedback(result)
                end = min(
                    self._finishes.values(), key=lambda p: (p[0], p[1].group[0].root), default=None
                )
                timer = self.next_feedback()
                now = self.session.runtime.now_offset_sec
                # 同秒真实完成先落事实，避免把它误认作超时；随后合并该时刻触发原因。
                if end and end[0] <= now:
                    at, stage = end
                    del self._finishes[stage.group]
                    self.observe("OPERATION_COMPLETED", stage, at)
                    continue
                if timer and timer[0] <= now:
                    self.update_remaining(timer)
                    continue
                if self._pending_reasons:
                    self.flush_replan()
                    continue
                stages = self.stages()
                start = min(stages, key=lambda s: (s.at, s.group[0].root), default=None)
                start_at = start.at if start else float("inf")
                end_at = end[0] if end else float("inf")
                timer_at = timer[0] if timer else float("inf")
                if end and end_at <= min(start_at, timer_at):
                    at, stage = end
                    del self._finishes[stage.group]
                    self.observe("OPERATION_COMPLETED", stage, at)
                elif timer and timer_at <= start_at:
                    self.update_remaining(timer)
                elif start:
                    self.start_stage(start)
                else:
                    raise DispatchFailure(
                        "持续反馈后仍无可派发阶段或完成反馈",
                        {"code": "OBSERVATION_REQUIRED", "blocked_stages": self._blocked_stages},
                    )
            raise DispatchFailure("持续反馈轨迹事件上限耗尽", {"code": "EVENT_LIMIT_EXHAUSTED"})
        except (ValueError, TimeoutError) as exc:
            self.failure_detail = (
                exc.detail
                if isinstance(exc, DispatchFailure)
                else {
                    "code": "TIMEOUT" if isinstance(exc, TimeoutError) else "FEEDBACK_FAILED",
                    "reason": str(exc),
                    "now_sec": self.session.runtime.now_offset_sec,
                }
            )
            self.diagnostic(self.failure_detail)
            return self.with_feedback(self.report(began, str(exc), None))

    def with_feedback(self, result: dict[str, object]) -> dict[str, object]:
        return {
            **result,
            "feedback_mode": "CONTINUOUS_VISIBLE_TIMER_V1",
            "feedback_source": "SYNTHETIC_NOT_COMPLETED_PLUS_DECLARED_ESTIMATE_NOT_MEASUREMENT",
            "feedback_extension_sec": self.config.get("feedback_extension_sec", 30),
            "feedback_triggers": self.feedback_triggers,
        }
