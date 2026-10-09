"""先落账再重排；单作业合并最新状态，冲突至多重算一次。"""

from _thread import LockType
from threading import Lock

from sqlalchemy.exc import OperationalError

from app.domain.candidates import stable_id
from app.domain.events import RuntimeEvent
from app.domain.ports import CpSatScheduler, Deadline
from app.domain.reports import PhaseTiming, PlanningFailure, PlanningResult
from app.domain.runtime_clock import clock_offset
from app.domain.runtime_planning import RuntimePlanningResult
from app.domain.runtime_session import RuntimeSession
from app.domain.schedule import PublishConflict, PublishContext, PublishedPlan, ValidatedSchedule
from app.runtime.replanning import prepare_replan
from app.runtime.schedule_clock import ClockExecutionService
from app.runtime.service import RuntimeService
from app.scheduling.budget import ComputationBudget
from app.services.preparation_budget import PreparationBudget
from app.services.publishing import PlanPublisher
from app.services.replanning import ReplanningService
from app.services.session_knowledge import session_knowledge
from app.storage.repositories import RuntimeRepository


class PlanningService:
    def __init__(
        self, runtime: RuntimeService, solver: CpSatScheduler, *, job_lock: LockType | None = None
    ) -> None:
        self.runtime, self.solver = runtime, solver
        self._job_lock = job_lock if job_lock is not None else Lock()

    def apply_event(
        self,
        event: RuntimeEvent,
        deadline: Deadline | None = None,
        *,
        preparation_budget: PreparationBudget | None = None,
    ) -> RuntimePlanningResult:
        started = self.runtime.clock.monotonic_ns()
        preparation_budget = preparation_budget or PreparationBudget(
            self.runtime.clock.monotonic_ns, started
        )
        overhead_started = preparation_budget.started_ns
        session = self.runtime.get(event.session_id.root)
        budget = (
            session.policy.initial_budget
            if event.event_type == "START_SESSION"
            else session.policy.replan_budget
        )
        deadline = deadline or Deadline(expires_at_ns=started + budget.total_ms * 1_000_000)
        result = self.runtime.apply_event(event, preparation_budget.extend(deadline))
        if result.status != "APPLIED":
            return RuntimePlanningResult(
                status="EVENT_REJECTED", event=result, budget_ms=budget.total_ms
            )
        outcome = self.drain(
            event.session_id.root,
            deadline,
            budget_ms=budget.total_ms,
            started_ns=started if overhead_started is None else overhead_started,
            preparation_budget=preparation_budget,
        )
        return outcome.model_copy(
            update={
                "event": result,
                "elapsed_ms": (
                    self.runtime.clock.monotonic_ns()
                    - (started if overhead_started is None else overhead_started)
                )
                // 1_000_000,
            }
        )

    def drain(
        self,
        session_id: str,
        deadline: Deadline | None = None,
        *,
        budget_ms: int | None = None,
        started_ns: int | None = None,
        preparation_budget: PreparationBudget | None = None,
    ) -> RuntimePlanningResult:
        """启动恢复或前轮预算用尽后，继续处理持久化的待重排标记。"""
        started_ns = self.runtime.clock.monotonic_ns() if started_ns is None else started_ns
        preparation_budget = preparation_budget or PreparationBudget(
            self.runtime.clock.monotonic_ns, started_ns
        )
        session = self.runtime.get(session_id)
        details = session.runtime.details
        budget = (
            session.policy.initial_budget
            if details and details.planning_kind == "INITIAL"
            else session.policy.replan_budget
        )
        total = budget.total_ms if budget_ms is None else budget_ms
        limit = deadline or Deadline(
            expires_at_ns=self.runtime.clock.monotonic_ns() + total * 1_000_000
        )
        with preparation_budget.measure(total) as clock_limit:
            session = ClockExecutionService(self.runtime).advance(session_id, deadline=clock_limit)
        if not session.requires_replan or session.status != "ACTIVE":
            return RuntimePlanningResult(
                status="NO_REPLAN", plan=self._plan(session), budget_ms=total
            )
        pending = self._waiting(session, total)
        if pending is not None:
            return pending
        # 事件线程不等待 Solver 锁；已提交状态中的标记就是恢复队列。
        if not self._job_lock.acquire(blocking=False):
            return RuntimePlanningResult(status="PENDING", budget_ms=total)
        try:
            result = self._compute_latest(session_id, limit, total, started_ns, preparation_budget)
            return result.model_copy(
                update={"elapsed_ms": (self.runtime.clock.monotonic_ns() - started_ns) // 1_000_000}
            )
        finally:
            self._job_lock.release()

    def _plan(self, session: RuntimeSession) -> PublishedPlan | None:
        with self.runtime.store.engine.connect() as tx:
            return RuntimeRepository(tx).plan(
                session.runtime.session_id.root, session.runtime.current_plan_version
            )

    def _waiting(self, session: RuntimeSession, budget_ms: int) -> RuntimePlanningResult | None:
        clock = session.schedule_clock
        if clock is None:
            return None
        blocked = (
            set(session.runtime.details.blocked_task_ids) if session.runtime.details else set()
        )
        if any(
            execution.status == "RUNNING"
            and (execution.interruption_event_refs or blocked.intersection(execution.task_ids))
            for execution in session.runtime.executions
        ):
            failure = PlanningResult(
                status="FAILED",
                failure=PlanningFailure(
                    code="STATE_INCOMPLETE",
                    failure_class="STATE_INCOMPLETE",
                    message="当前工序受到异常影响；请确认实际结果或提供恢复反馈后再重排。",
                ),
            )
            recorded = self._record_failure(session, failure)
            return RuntimePlanningResult(
                status="FAILED" if recorded else "PENDING",
                planning=failure if recorded else None,
                budget_ms=budget_ms,
                replan_requested_sec=clock.replan_requested_sec,
                replan_not_before_sec=clock.replan_not_before_sec,
            )
        # 旧会话保存的边界仅用于必要衔接的派发；运行事实由剩余问题冻结，
        # 不再等待整道加热结束才求解和发布未来计划。
        return None

    def _compute_latest(
        self,
        session_id: str,
        deadline: Deadline,
        budget_ms: int,
        started_ns: int,
        preparation_budget: PreparationBudget,
    ) -> RuntimePlanningResult:
        attempts = 0
        initial = self.runtime.get(session_id)
        allowance = ComputationBudget.from_spec(
            initial.policy.initial_budget
            if initial.runtime.details and initial.runtime.details.planning_kind == "INITIAL"
            else initial.policy.replan_budget
        )
        # SQL 发布还要独立复核、绑定预约和归档回执。策略的 Solver 数值是上限；
        # 这里缩短可选优化的实际配额，不改总截止时间，也不删减工序或检查。
        is_initial = initial.runtime.details and initial.runtime.details.planning_kind == "INITIAL"
        allocation = (3, 5) if is_initial else (11, 15)
        if not initial.policy.quality_first:
            allowance.solver_remaining_ns = (
                allowance.solver_remaining_ns * allocation[0] // allocation[1]
            )
        for attempt in range(2):
            if (
                self.runtime.clock.monotonic_ns()
                >= preparation_budget.extend(deadline).expires_at_ns
            ):
                break
            allowance.attempts_remaining = 2 - attempt
            with preparation_budget.measure(budget_ms) as clock_limit:
                session = ClockExecutionService(self.runtime).advance(
                    session_id, deadline=clock_limit
                )
            if not session.requires_replan or session.status != "ACTIVE":
                return RuntimePlanningResult(
                    status="NO_REPLAN",
                    plan=self._plan(session),
                    attempts=attempts,
                    budget_ms=budget_ms,
                )
            pending = self._waiting(session, budget_ms)
            if pending is not None:
                return pending
            attempts = attempt + 1
            elapsed = self.runtime.clock.now() - session.runtime.time_origin.start_at
            # 业务时间在边界向下取整到整数秒，原始反馈时刻和观测起点保持不变。
            now = max(
                session.runtime.now_offset_sec,
                clock_offset(session, self.runtime.clock.now())
                if session.schedule_clock is not None
                else elapsed.days * 86_400 + elapsed.seconds,
            )
            session = session.model_copy(
                update={"runtime": session.runtime.model_copy(update={"now_offset_sec": now})}
            )
            if session.schedule_clock is not None:
                # 新动作给当前请求剩余的计算、校验和事务预算留出生效时间。
                # 只修改不可变求解投影，不把未来时刻写成已发生的运行事实。
                remaining_ns = max(
                    0,
                    preparation_budget.extend(deadline).expires_at_ns
                    - self.runtime.clock.monotonic_ns(),
                )
                # 编译不扣求解余额，但仍消耗真实时间；其独立上限也要为新动作预留。
                compile_ms = (
                    session.policy.initial_budget.compilation_limit_ms
                    if is_initial
                    else session.policy.replan_budget.compilation_limit_ms
                )
                effective = (
                    now + (remaining_ns + compile_ms * 1_000_000 + 999_999_999) // 1_000_000_000 + 1
                )
                continuation = set(session.schedule_clock.replan_continuation_task_ids)
                required_starts = [
                    span.interval.start_sec
                    for binding in session.bindings
                    for span in binding.task_spans
                    if span.task_id in continuation and span.interval.start_sec >= now
                ]
                if required_starts:
                    effective = min(effective, *required_starts)
                details = session.runtime.details
                assert details is not None
                starts = dict(details.earliest_starts)
                for instance in session.menu:
                    key = instance.recipe_instance_id.root
                    starts[key] = max(starts.get(key, 0), effective)
                session = session.model_copy(
                    update={
                        "runtime": session.runtime.model_copy(
                            update={
                                "details": details.model_copy(
                                    update={"earliest_starts": tuple(sorted(starts.items()))}
                                )
                            }
                        )
                    }
                )
            previous = self._plan(session)
            planner = ReplanningService(
                session_knowledge(self.runtime.knowledge, session.menu),
                self.solver,
                previous.validated.candidate if previous else None,
                computation_budget=allowance,
            )
            try:
                request = prepare_replan(session, None, self.runtime.knowledge, session.policy)
            except ValueError as exc:
                result = PlanningResult(
                    status="FAILED",
                    failure=PlanningFailure(
                        code="STATE_INCOMPLETE", failure_class="STATE_INCOMPLETE", message=str(exc)
                    ),
                )
            else:
                result = planner.compute(request, deadline, preparation_budget=preparation_budget)
            if result.status == "FAILED":
                if self._record_failure(session, result):
                    return RuntimePlanningResult(
                        status="FAILED", planning=result, attempts=attempts, budget_ms=budget_ms
                    )
                continue
            assert (
                planner.last_problem is not None
                and result.candidate is not None
                and result.validation is not None
            )
            state = session.runtime
            identity = stable_id(
                "publication",
                session_id,
                str(state.state_revision),
                str(state.current_plan_version),
            )
            context = PublishContext(
                session_id=state.session_id,
                base_state_revision=state.state_revision,
                base_plan_version=state.current_plan_version,
                knowledge_version=state.knowledge_version,
                snapshot_id=state.snapshot_id,
                request_id=f"replan-{session_id}-{state.state_revision}",
                publication_id=identity,
            )
            publisher = PlanPublisher(
                self.runtime.store,
                self.runtime.knowledge,
                self.runtime.clock,
                planner.last_problem,
                planning_started_ns=started_ns,
                serial_reference=ValidatedSchedule(
                    candidate=result.serial_reference_candidate,
                    validation=result.serial_reference_validation,
                )
                if result.serial_reference_candidate is not None
                and result.serial_reference_validation is not None
                else None,
            )
            publication_started = self.runtime.clock.monotonic_ns()
            candidate_identity = result.candidate.candidate_hash

            def record_publication_timing(
                result: PlanningResult, started_ns: int = publication_started
            ) -> PlanningResult:
                return result.model_copy(
                    update={
                        "timings": (
                            *result.timings,
                            PhaseTiming(
                                stage="PUBLICATION",
                                elapsed_ms=(self.runtime.clock.monotonic_ns() - started_ns)
                                // 1_000_000,
                            ),
                        )
                    }
                )

            try:
                published = publisher.publish(
                    ValidatedSchedule(candidate=result.candidate, validation=result.validation),
                    context,
                    deadline=preparation_budget.extend(deadline),
                )
            except (TimeoutError, InterruptedError, ConnectionError, OperationalError) as exc:
                result = record_publication_timing(result)
                # 提交后的响应错误不能用内存异常推断回滚。查询既定身份，再决定返回结果。
                try:
                    with self.runtime.store.engine.connect() as tx:
                        recovered = RuntimeRepository(tx).publication(identity)
                except OperationalError:
                    return RuntimePlanningResult(
                        status="PENDING", attempts=attempts, budget_ms=budget_ms
                    )
                if recovered is not None:
                    if (
                        recovered.session_id != context.session_id
                        or recovered.validated.candidate.candidate_hash != candidate_identity
                    ):
                        return RuntimePlanningResult(
                            status="PENDING", attempts=attempts, budget_ms=budget_ms
                        )
                    return RuntimePlanningResult(
                        status="PUBLISHED",
                        plan=recovered,
                        planning=result,
                        attempts=attempts,
                        budget_ms=budget_ms,
                    )
                failure = result.model_copy(
                    update={
                        "status": "FAILED",
                        "candidate": None,
                        "validation": None,
                        "failure": PlanningFailure(
                            code="NO_FEASIBLE_PLAN",
                            failure_class="NO_SOLUTION_WITHIN_BUDGET",
                            message="计划未提交：" + str(exc),
                            evidence_refs=(identity,),
                        ),
                    }
                )
                try:
                    recorded = self._record_failure(session, failure)
                except OperationalError:
                    return RuntimePlanningResult(
                        status="PENDING", attempts=attempts, budget_ms=budget_ms
                    )
                if recorded:
                    return RuntimePlanningResult(
                        status="FAILED", planning=failure, attempts=attempts, budget_ms=budget_ms
                    )
                continue
            if not isinstance(published, PublishConflict):
                result = record_publication_timing(result)
                return RuntimePlanningResult(
                    status="PUBLISHED",
                    plan=published,
                    planning=result,
                    attempts=attempts,
                    budget_ms=budget_ms,
                )
        return RuntimePlanningResult(status="PENDING", attempts=attempts, budget_ms=budget_ms)

    def _record_failure(self, base: RuntimeSession, result: PlanningResult) -> bool:
        assert result.failure is not None
        with self.runtime.store.transaction() as tx:
            repo = RuntimeRepository(tx)
            current = repo.get(base.runtime.session_id.root)
            if (current.runtime.state_revision, current.runtime.current_plan_version) != (
                base.runtime.state_revision,
                base.runtime.current_plan_version,
            ):
                return False
            repo.save(
                current.model_copy(
                    update={
                        "dispatch_blocked": True,
                        "last_planning_failure": result.failure.message,
                    }
                ),
                expected_revision=base.runtime.state_revision,
                expected_plan=base.runtime.current_plan_version,
            )
            return True
