"""事件只在短事务中落事实；求解由应用编排层在事务外调用。"""

from datetime import datetime

from sqlalchemy.exc import OperationalError

from app.domain.candidates import stable_id
from app.domain.critical_windows import recipe_sequence_dependencies
from app.domain.events import (
    EventApplyResult,
    EventSource,
    ExecutionPayload,
    OverridePayload,
    RuntimeEvent,
    SimulationPayload,
)
from app.domain.ids import SessionId
from app.domain.knowledge import MenuKnowledgeView
from app.domain.policy import SchedulingPolicy
from app.domain.ports import Clock, Deadline
from app.domain.runtime_facts import RuntimeDetails
from app.domain.runtime_session import RuntimeSession
from app.domain.runtime_snapshot import RuntimeSnapshot
from app.domain.time import TimeOrigin
from app.runtime.device_events import apply_device
from app.runtime.event_validation import EventConflict, event_digest, validate_event
from app.runtime.execution_failures import request_retry
from app.runtime.inventory import observe_inventory
from app.runtime.material_ledger import apply_material_effects
from app.runtime.material_reports import source_specs, validate_reports
from app.runtime.material_reservations import materialize, release_future
from app.runtime.menu_events import apply_menu
from app.runtime.notifications import NotificationService
from app.runtime.replan_boundary import defer_replan
from app.runtime.state_machine import apply_execution
from app.runtime.triggers import replan_reasons
from app.storage.repositories import RuntimeRepository
from app.storage.unit_of_work import UnitOfWork


class RuntimeService:
    def __init__(self, store: UnitOfWork, knowledge: MenuKnowledgeView, clock: Clock) -> None:
        self.store, self.knowledge, self.clock = store, knowledge, clock
        # 只缓存一次完整扫描的不可变会话；任一事实、计划或派发状态变化即失效。
        self.clock_scan_cache: tuple[int, RuntimeSession] | None = None

    def create_session(
        self, session_id: str, mode: EventSource | str, start_at: datetime, policy: SchedulingPolicy
    ) -> RuntimeSession:
        state = RuntimeSnapshot(
            session_id=SessionId(session_id),
            state_revision=0,
            current_plan_version=0,
            knowledge_version=self.knowledge.release.knowledge_version,
            rule_version=self.knowledge.release.rule_version,
            snapshot_id=self.knowledge.release.snapshot_id,
            time_origin=TimeOrigin(start_at=start_at),
            now_offset_sec=0,
            execution_mode=EventSource(mode),
            details=RuntimeDetails(),
        )
        session = RuntimeSession(
            runtime=state, policy=policy, knowledge_release_id=self.knowledge.release.release_id
        )
        with self.store.transaction() as tx:
            repo = RuntimeRepository(tx)
            repo.remember_release(self.knowledge.release)
            repo.save(session)
        return session

    def get(self, session_id: str) -> RuntimeSession:
        with self.store.engine.connect() as tx:
            session = RuntimeRepository(tx).get(session_id)
        self._check_knowledge(session)
        return session

    def _check_knowledge(self, session: RuntimeSession) -> None:
        state, release = session.runtime, self.knowledge.release
        if (
            state.knowledge_version,
            state.rule_version,
            state.snapshot_id,
            session.knowledge_release_id,
        ) != (
            release.knowledge_version,
            release.rule_version,
            release.snapshot_id,
            release.release_id,
        ):
            raise ValueError("运行服务尚未加载会话固定知识版本")

    def apply_event(
        self, event: RuntimeEvent, deadline: Deadline | None = None
    ) -> EventApplyResult:
        limit = deadline.expires_at_ns if deadline else self.clock.monotonic_ns() + 400_000_000
        for attempt in range(3):
            try:
                if self.clock.monotonic_ns() >= limit:
                    raise TimeoutError("事件事务预算不足")
                return self._apply(event)
            except OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == 2:
                    raise
        raise TimeoutError("事件事务未提交")

    def _apply(self, event: RuntimeEvent) -> EventApplyResult:
        digest = event_digest(event)
        with self.store.transaction() as tx:
            repo = RuntimeRepository(tx)
            prior = repo.event(event.event_id.root)
            if prior:
                if prior[0] == digest:
                    return prior[1]
                return EventApplyResult(
                    event_id=event.event_id,
                    first_applied=False,
                    status="CONFLICT",
                    state_revision=repo.get(event.session_id.root).runtime.state_revision,
                    rejection_reason="同一事件 ID 的内容不同",
                )
            session = repo.get(event.session_id.root)
            self._check_knowledge(session)
            admitted = False
            try:
                validate_event(event, session)
                admitted = True
                changed = self._transition(session, event)
                reasons = replan_reasons(session, changed, event, self.knowledge)
            except ValueError as exc:
                conflict = isinstance(exc, EventConflict)
                pause = admitted and conflict and isinstance(event.payload, ExecutionPayload)
                rejected_state = session.runtime
                if pause:
                    assert isinstance(event.payload, ExecutionPayload)
                    conflicted_task = event.payload.task_id
                    details = rejected_state.details
                    assert details is not None
                    affected = next(
                        (
                            b.assignment.task_ids
                            for b in session.bindings
                            if conflicted_task in b.assignment.task_ids
                        ),
                        (conflicted_task,),
                    )
                    rejected_state = rejected_state.model_copy(
                        update={
                            "state_revision": rejected_state.state_revision + 1,
                            "details": details.model_copy(
                                update={
                                    "blocked_task_ids": tuple(
                                        dict.fromkeys((*details.blocked_task_ids, *affected))
                                    )
                                }
                            ),
                        }
                    )
                result = EventApplyResult(
                    event_id=event.event_id,
                    first_applied=False,
                    status="CONFLICT" if conflict else "REJECTED",
                    state_revision=rejected_state.state_revision,
                    rejection_reason=str(exc),
                )
                repo.record_event(event, digest, result)
                repo.audit(
                    "rejected-" + event.event_id.root,
                    event.session_id.root,
                    event.model_dump_json(),
                )
                if pause:
                    repo.save(
                        session.model_copy(
                            update={
                                "runtime": rejected_state,
                                "dispatch_blocked": True,
                                "last_planning_failure": str(exc),
                            }
                        ),
                        expected_revision=session.runtime.state_revision,
                    )
                    NotificationService().observe(tx, event, rejected_state, dispatch_blocked=True)
                return result
            state = changed.runtime
            details = state.details
            assert details is not None
            offset = max(state.now_offset_sec, state.time_origin.offset(event.occurred_at))
            if isinstance(event.payload, SimulationPayload):
                offset = max(offset, session.runtime.now_offset_sec + event.payload.advance_sec)
            state = state.model_copy(
                update={
                    "state_revision": session.runtime.state_revision + 1,
                    "now_offset_sec": offset,
                    "event_refs": (*state.event_refs, event.event_id),
                    "details": details.model_copy(
                        update={
                            "planning_kind": "INITIAL"
                            if event.event_type == "START_SESSION"
                            else "REPLAN"
                        }
                    ),
                }
            )
            changed = changed.model_copy(
                update={
                    "runtime": state,
                    "requires_replan": changed.status == "ACTIVE"
                    and (bool(reasons) or session.requires_replan),
                    "replan_reasons": tuple(dict.fromkeys((*session.replan_reasons, *reasons)))
                    if changed.status == "ACTIVE"
                    else (),
                    "dispatch_blocked": changed.dispatch_blocked or bool(reasons),
                    "last_planning_failure": None if reasons else session.last_planning_failure,
                }
            )
            changed = defer_replan(
                changed,
                requested_sec=offset if reasons else None,
                problem=repo.problem(event.session_id.root, state.current_plan_version)
                if changed.schedule_clock is not None
                and changed.requires_replan
                and state.current_plan_version > 0
                else None,
            )
            result = EventApplyResult(
                event_id=event.event_id,
                first_applied=True,
                status="APPLIED",
                state_revision=state.state_revision,
                fact_change_refs=(event.event_id.root,),
                requires_replan=bool(reasons),
                replan_reasons=reasons,
            )
            changed = observe_inventory(changed, event)
            if reasons or changed.status == "ENDED":
                changed = release_future(changed, event.event_id.root, event.event_id.root)
            changed = materialize(changed, event.event_id.root, event.event_id.root)
            repo.record_event(event, digest, result)
            repo.save(
                changed,
                expected_revision=session.runtime.state_revision,
                expected_plan=session.runtime.current_plan_version,
            )
            NotificationService().observe(
                tx, event, state, dispatch_blocked=changed.dispatch_blocked
            )
            return result

    def _transition(self, session: RuntimeSession, event: RuntimeEvent) -> RuntimeSession:
        kind = event.event_type
        if kind in {"START_SESSION", "ADD_RECIPE", "CANCEL_RECIPE", "DELAY_RECIPE"}:
            return apply_menu(session, event, self.knowledge)
        if kind.value.startswith("DEVICE_"):
            return apply_device(session, event, self.knowledge)
        if kind in {"MATERIAL_SHORTAGE", "MATERIAL_ADJUSTED"}:
            return apply_material_effects(event, session)
        if kind == "OPERATION_RETRY_REQUESTED":
            return request_retry(session, event, self.knowledge)
        if kind == "MANUAL_OVERRIDE":
            return self._override(session, event)
        if kind == "RESET_SESSION":
            return session.model_copy(
                update={
                    "status": "ENDED",
                    "dispatch_blocked": True,
                    "requires_replan": False,
                    "replan_reasons": (),
                }
            )
        if kind in {"ADVANCE_SIMULATION", "REPLAN_REQUESTED"}:
            return session
        dependencies = []
        recipes = {r.recipe_id: r for r in self.knowledge.recipes}
        for instance in session.menu:
            for dep in recipes[instance.recipe_id].dependencies:
                dependencies.append(
                    (
                        stable_id(
                            "task", instance.recipe_instance_id.root, dep.predecessor_id.root
                        ),
                        stable_id("task", instance.recipe_instance_id.root, dep.successor_id.root),
                        dep.min_lag_sec,
                        dep.max_lag_sec,
                    )
                )
        cancelled = (
            set(session.runtime.details.cancelled_instance_ids)
            if session.runtime.details
            else set()
        )
        dependencies.extend(
            (d.predecessor_id.root, d.successor_id.root, d.min_lag_sec, d.max_lag_sec)
            for d in recipe_sequence_dependencies(
                tuple(i for i in session.menu if i.recipe_instance_id.root not in cancelled),
                self.knowledge,
                session.policy,
            )
        )
        validate_reports(session, event, self.knowledge)
        return apply_execution(
            session,
            event,
            tuple(dependencies),
            self._minimum_durations(session),
            source_specs(session, self.knowledge),
            self.knowledge.devices,
        )

    def _minimum_durations(self, session: RuntimeSession) -> tuple[tuple[str, int], ...]:
        recipes = {r.recipe_id: r for r in self.knowledge.recipes}
        minima = []
        for instance in session.menu:
            for operation in recipes[instance.recipe_id].operations:
                duration = operation.duration
                minimum = duration.lower_sec
                if minimum is None and (
                    duration.fixed_process_time
                    or operation.action in {"MARINATE", "WAIT", "CHILL", "FREEZE"}
                ):
                    minimum = duration.execution_sec
                if minimum is not None:
                    minima.append(
                        (
                            stable_id(
                                "task",
                                instance.recipe_instance_id.root,
                                operation.operation_id.root,
                            ),
                            minimum,
                        )
                    )
        return tuple(minima)

    def _override(self, session: RuntimeSession, event: RuntimeEvent) -> RuntimeSession:
        payload = event.payload
        assert isinstance(payload, OverridePayload)
        if not payload.evidence_refs:
            raise ValueError("修正必须提供依据")
        if payload.correction_type == "REMAINING_DURATION":
            record = next(
                (e for e in session.runtime.executions if e.execution_id.root == payload.target_id),
                None,
            )
            if record is None or record.remaining_sec != payload.before_value:
                raise ValueError("剩余时间修正前值不符")
            derived = event.model_copy(
                update={
                    "event_type": "DURATION_UPDATED",
                    "payload": ExecutionPayload(
                        task_id=record.task_ids[0],
                        execution_id=record.execution_id,
                        remaining_sec=payload.after_value,
                    ),
                }
            )
            return apply_execution(
                session,
                RuntimeEvent.model_validate(derived.model_dump()),
                (),
                self._minimum_durations(session),
                devices=self.knowledge.devices,
            )
        details = session.runtime.details
        assert details is not None
        if payload.target_id not in {i.recipe_instance_id.root for i in session.menu}:
            raise ValueError("偏好只能修改存在的菜谱实例")
        starts = dict(details.earliest_starts)
        if starts.get(payload.target_id, 0) != payload.before_value:
            raise ValueError("偏好修正前值不符")
        starts[payload.target_id] = payload.after_value
        return session.model_copy(
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
