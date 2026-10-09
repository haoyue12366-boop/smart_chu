"""校验在事务外，版本 CAS、计划、预约与通知在同一短事务提交。"""

from sqlalchemy import insert, update
from sqlalchemy.exc import OperationalError

from app.domain.carrier_timing import task_intervals
from app.domain.knowledge import MenuKnowledgeView
from app.domain.planning_timing import PlanningOverhead
from app.domain.ports import Clock, Deadline
from app.domain.runtime_clock import ScheduleClockState, clock_offset
from app.domain.runtime_facts import TaskSpan
from app.domain.runtime_session import ExecutionBinding
from app.domain.schedule import PublishConflict, PublishContext, PublishedPlan, ValidatedSchedule
from app.domain.scheduling_problem import SchedulingProblem
from app.runtime.inventory import prepare_inventory
from app.runtime.material_reservations import plan_reservations
from app.runtime.notification_templates import plan_change_notice
from app.runtime.notifications import NotificationService
from app.storage import models
from app.storage.notification_stream import append_message
from app.storage.repositories import RuntimeRepository
from app.storage.unit_of_work import UnitOfWork
from app.validation.schedule import ScheduleValidator


class PlanPublisher:
    def __init__(
        self,
        store: UnitOfWork,
        knowledge: MenuKnowledgeView,
        clock: Clock,
        problem: SchedulingProblem,
        *,
        serial_reference: ValidatedSchedule | None = None,
        planning_started_ns: int | None = None,
    ) -> None:
        self.store, self.knowledge, self.clock, self.problem = store, knowledge, clock, problem
        self.serial_reference = serial_reference
        self.planning_started_ns = planning_started_ns

    def publish(
        self,
        candidate: ValidatedSchedule,
        context: PublishContext,
        *,
        deadline: Deadline | None = None,
    ) -> PublishedPlan | PublishConflict:
        limit = deadline or Deadline(expires_at_ns=self.clock.monotonic_ns() + 400_000_000)
        for attempt in range(3):
            try:
                return self._publish(candidate, context, limit)
            except OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == 2:
                    raise
        raise TimeoutError("计划事务未提交")

    def _publish(
        self, candidate: ValidatedSchedule, context: PublishContext, deadline: Deadline
    ) -> PublishedPlan | PublishConflict:
        # 已提交发布身份的重试不再次生成通知，也不依赖易失内存的成功标记。
        with self.store.engine.connect() as tx:
            old = RuntimeRepository(tx).publication(context.publication_id)
        if old is not None:
            if (
                old.session_id != context.session_id
                or old.validated.candidate.candidate_hash != candidate.candidate.candidate_hash
            ):
                return PublishConflict(
                    publication_id=context.publication_id,
                    reason="发布身份内容冲突",
                    current_state_revision=old.state_revision,
                    current_plan_version=old.plan_version,
                )
            return old
        if self.clock.monotonic_ns() >= deadline.expires_at_ns:
            raise TimeoutError("发布截止时间已到")
        state = self.problem.runtime
        if (state.session_id, state.state_revision, state.current_plan_version) != (
            context.session_id,
            context.base_state_revision,
            context.base_plan_version,
        ):
            raise ValueError("发布上下文不是当前编译问题绑定的会话与版本")
        proof = ScheduleValidator().validate(
            self.knowledge, self.problem.runtime, self.problem, candidate.candidate
        )
        if self.serial_reference is not None:
            # 本轮新扫描已经核验完全相同的不可变候选时，它也证明串行参考的约束。
            # 不读取调用者的旧 proof；不同参考仍单独扫描，串行顺序继续独立核对。
            reference_proof = (
                proof
                if self.serial_reference.candidate == candidate.candidate
                else ScheduleValidator().validate(
                    self.knowledge,
                    self.problem.runtime,
                    self.problem,
                    self.serial_reference.candidate,
                )
            )
            from app.scheduling.serial_reference import serial_order_holds

            if not reference_proof.valid or not serial_order_holds(
                self.serial_reference.candidate, self.problem
            ):
                raise ValueError("串行参考没有通过当前状态的独立核验")
        if not proof.valid or (context.knowledge_version, context.snapshot_id) != (
            self.problem.knowledge_version,
            self.problem.snapshot_id,
        ):
            raise ValueError("候选未通过独立校验或知识版本不一致")
        ports = task_intervals(self.problem, candidate.candidate.assignments)
        carriers = {
            c.carrier_id: c
            for c in (
                *self.problem.standalone_candidates,
                *self.problem.shared_prep_candidates,
                *self.problem.thermal_batch_candidates,
                *self.problem.inventory_supply_candidates,
            )
        }
        with self.store.transaction() as tx:
            repo = RuntimeRepository(tx)
            prior = repo.publication(context.publication_id)
            if prior is not None:
                if (
                    prior.session_id != context.session_id
                    or prior.validated.candidate.candidate_hash
                    != candidate.candidate.candidate_hash
                ):
                    return PublishConflict(
                        publication_id=context.publication_id,
                        reason="发布身份内容冲突",
                        current_state_revision=prior.state_revision,
                        current_plan_version=prior.plan_version,
                    )
                return prior
            session = repo.get(context.session_id.root)
            state = session.runtime
            if (
                state.state_revision,
                state.current_plan_version,
                state.knowledge_version,
                state.snapshot_id,
            ) != (
                context.base_state_revision,
                context.base_plan_version,
                context.knowledge_version,
                context.snapshot_id,
            ):
                return PublishConflict(
                    publication_id=context.publication_id,
                    reason="求解期间事实或计划已经变化",
                    current_state_revision=state.state_revision,
                    current_plan_version=state.current_plan_version,
                )
            if session.status != "ACTIVE":
                raise ValueError("结束的会话不能发布新计划")
            if session.policy != self.problem.policy:
                raise ValueError("计划策略与固定会话策略不一致")
            cancelled = set(state.details.cancelled_instance_ids) if state.details else set()
            expected_menu = tuple(
                instance
                for instance in session.menu
                if instance.recipe_instance_id.root not in cancelled
            )
            if self.problem.recipe_instances != expected_menu:
                raise ValueError("候选没有绑定会话的完整菜单")
            committed_at = self.clock.now()
            if session.schedule_clock is not None and any(
                assignment.interval.start_sec < clock_offset(session, committed_at)
                for assignment in candidate.candidate.assignments
            ):
                return PublishConflict(
                    publication_id=context.publication_id,
                    reason="求解期间新动作的生效时间已过去，须同步进度后重算",
                    current_state_revision=state.state_revision,
                    current_plan_version=state.current_plan_version,
                )
            published = PublishedPlan(
                session_id=context.session_id,
                plan_version=context.base_plan_version + 1,
                parent_plan_version=context.base_plan_version,
                state_revision=context.base_state_revision,
                knowledge_version=context.knowledge_version,
                snapshot_id=context.snapshot_id,
                time_origin=state.time_origin,
                validated=ValidatedSchedule(candidate=candidate.candidate, validation=proof),
                publication_id=context.publication_id,
                committed_at=committed_at,
                serial_reference=self.serial_reference,
            )
            active_ids = {e.carrier_id for e in state.executions if e.status == "RUNNING"}
            bindings = tuple(b for b in session.bindings if b.carrier.carrier_id.root in active_ids)
            bindings += tuple(
                ExecutionBinding(
                    assignment=a,
                    carrier=carriers[a.carrier_id],
                    plan_version=published.plan_version,
                    task_spans=tuple(TaskSpan(task_id=t, interval=ports[t]) for t in a.task_ids),
                    continuities=tuple(
                        reservation
                        for reservation in self.problem.mandatory_programs.reservations
                        if set(reservation.members) & set(a.task_ids)
                        and reservation.reservation_id
                        not in carriers[a.carrier_id].replaced_reservation_ids
                    ),
                )
                for a in candidate.candidate.assignments
            )
            # 已完成映射也保留，用于审计迟到反馈；同一未开始任务由新映射取代。
            new_tasks = {t for b in bindings for t in b.assignment.task_ids}
            bindings += tuple(
                b for b in session.bindings if not set(b.assignment.task_ids) & new_tasks
            )
            state = state.model_copy(
                update={
                    "current_plan_version": published.plan_version,
                    "current_plan_ref": context.publication_id,
                }
            )
            changed = session.model_copy(
                update={
                    "runtime": state,
                    "bindings": bindings,
                    "requires_replan": False,
                    "replan_reasons": (),
                    "dispatch_blocked": False,
                    "last_planning_failure": None,
                    "schedule_clock": (
                        session.schedule_clock.model_copy(
                            update={
                                "replan_requested_sec": None,
                                "replan_not_before_sec": None,
                                "replan_continuation_task_ids": (),
                            }
                        )
                        if session.schedule_clock is not None
                        else ScheduleClockState(
                            started_at=published.committed_at,
                            start_offset_sec=self.problem.runtime.now_offset_sec,
                            processed_until_sec=self.problem.runtime.now_offset_sec,
                        )
                        if state.execution_mode == "SCHEDULE_CLOCK"
                        else None
                    ),
                }
            )
            changed = prepare_inventory(changed, self.problem, published)
            changed = plan_reservations(
                changed,
                self.problem,
                {
                    t
                    for a in candidate.candidate.assignments
                    if carriers[a.carrier_id].kind != "INVENTORY_SUPPLY"
                    for t in a.task_ids
                },
                published.plan_version,
                context.publication_id,
            )
            tx.execute(
                insert(models.plans).values(
                    publication_id=context.publication_id,
                    session_id=context.session_id.root,
                    version=published.plan_version,
                    body=published.model_dump_json(),
                    problem=self.problem.model_dump_json(),
                )
            )
            NotificationService(self.problem).persist(tx, published, state)
            append_message(
                tx,
                context.session_id.root,
                "plan-changed:" + published.publication_id,
                plan_change_notice(published),
            )
            repo.save(
                changed,
                expected_revision=context.base_state_revision,
                expected_plan=context.base_plan_version,
            )
            if self.clock.monotonic_ns() >= deadline.expires_at_ns:
                raise TimeoutError("发布事务超过截止时间，已回滚")
            if self.planning_started_ns is not None:
                details = self.problem.runtime.details
                published = published.model_copy(
                    update={
                        "planning_overhead": PlanningOverhead(
                            kind="INITIAL"
                            if details and details.planning_kind == "INITIAL"
                            else "REPLAN",
                            elapsed_ms=(self.clock.monotonic_ns() - self.planning_started_ns)
                            // 1_000_000,
                        )
                    }
                )
                tx.execute(
                    update(models.plans)
                    .where(models.plans.c.publication_id == context.publication_id)
                    .values(body=published.model_dump_json())
                )
            if self.clock.monotonic_ns() >= deadline.expires_at_ns:
                raise TimeoutError("发布事务超过截止时间，已回滚")
            return published
