"""生命周期持有版本化知识、状态库和唯一求解工作进程。"""

import json
from _thread import LockType
from threading import Event, Lock

from sqlalchemy import func, literal_column, select

from app.config import AppSettings
from app.domain.knowledge import MenuKnowledgeView
from app.domain.policy import SchedulingPolicy
from app.domain.ports import Deadline
from app.domain.runtime_session import RuntimeSession
from app.knowledge.loader import read_release_ref
from app.knowledge.repository import SnapshotKnowledgeRepository
from app.llm.intent_service import IntentService
from app.runtime.clock import SimulationClock, SystemClock
from app.runtime.schedule_clock import ClockExecutionService
from app.runtime.service import RuntimeService
from app.scheduling.json_worker import JsonSolverWorker
from app.services.planning import PlanningService
from app.services.recovery_guard import isolated_recovery
from app.storage import models
from app.storage.repositories import RuntimeRepository
from app.storage.unit_of_work import UnitOfWork


class ServiceContainer:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self.clock = SystemClock()
        self.repository = SnapshotKnowledgeRepository(settings.release_root)
        self.worker = JsonSolverWorker()
        self.job_lock = Lock()
        self.runtimes: dict[str, RuntimeService] = {}
        self.planners: dict[str, PlanningService] = {}
        self.simulated_sessions: dict[str, tuple[RuntimeService, PlanningService]] = {}
        self.simulation_locks: dict[str, LockType] = {}
        self.clock_locks: dict[str, LockType] = {}
        self.request_locks: dict[str, LockType] = {}
        self.foreground_requests = 0
        self.ready = False
        self.stopping = Event()
        self.intents = IntentService(
            settings.language_archive_path, enabled=settings.language_enabled
        )

    def start(self) -> None:
        self.stopping.clear()
        self.policy = SchedulingPolicy.model_validate_json(
            self.settings.policy_path.read_text(encoding="utf-8")
        )
        self.store = UnitOfWork(self.settings.database_path)
        self.store.migrate()
        with self.store.engine.connect() as tx:
            stored = tuple(tx.execute(select(models.knowledge.c.body)).scalars())
        release_ids = {self.settings.release_id}
        release_ids.update(str(json.loads(body)["release_id"]) for body in stored)
        for release_id in sorted(release_ids):
            ref = read_release_ref(self.settings.release_root, release_id)
            self.repository.load(ref)
            with self.repository.acquire(ref) as lease:
                knowledge = lease.select(
                    tuple(r.recipe_id for r in lease.loaded.snapshot.knowledge.recipes)
                )
            runtime = RuntimeService(self.store, knowledge, self.clock)
            self.runtimes[release_id] = runtime
            self.planners[release_id] = PlanningService(
                runtime, self.worker, job_lock=self.job_lock
            )
        self.knowledge = self.runtimes[self.settings.release_id].knowledge
        if not self.worker.warmup():
            raise TimeoutError("求解进程未就绪")
        self.ready = True

    @property
    def runtime(self) -> RuntimeService:
        return self.runtimes[self.settings.release_id]

    @property
    def planner(self) -> PlanningService:
        return self.planners[self.settings.release_id]

    def for_session(self, session_id: str) -> tuple[RuntimeService, PlanningService]:
        with self.store.engine.connect() as tx:
            session = RuntimeRepository(tx).get(session_id)
        release_id = session.knowledge_release_id
        if release_id is None or release_id not in self.runtimes:
            raise ValueError("会话绑定的知识版本尚未加载")
        if session.runtime.execution_mode == "SIMULATED":
            if session_id not in self.simulated_sessions:
                clock = SimulationClock(session.runtime.time_origin.start_at)
                runtime = RuntimeService(self.store, self.runtimes[release_id].knowledge, clock)
                planner = PlanningService(runtime, self.worker, job_lock=self.job_lock)
                self.simulated_sessions[session_id] = runtime, planner
            runtime, planner = self.simulated_sessions[session_id]
            assert isinstance(runtime.clock, SimulationClock)
            runtime.clock.advance(max(runtime.clock.offset_sec, session.runtime.now_offset_sec))
            return runtime, planner
        return self.runtimes[release_id], self.planners[release_id]

    def knowledge_for(self, session_id: str) -> MenuKnowledgeView:
        return self.for_session(session_id)[0].knowledge

    def advance_clock(self, session_id: str, deadline: Deadline | None = None) -> RuntimeSession:
        """在事件准入前或后台同步已流逝的时钟，串行处理同一会话的边界。"""
        with self.clock_locks.setdefault(session_id, Lock()):
            runtime, _ = self.for_session(session_id)
            return ClockExecutionService(runtime).advance(session_id, deadline=deadline)

    def clock_sessions(self) -> tuple[str, ...]:
        with self.store.engine.connect() as tx:
            return tuple(
                tx.execute(
                    select(models.sessions.c.session_id).where(
                        func.json_extract(models.sessions.c.body, "$.status") == "ACTIVE",
                        func.json_extract(models.sessions.c.body, "$.runtime.execution_mode")
                        == "SCHEDULE_CLOCK",
                        func.json_extract(models.sessions.c.body, "$.schedule_clock").is_not(None),
                    )
                ).scalars()
            )

    def pending_sessions(self) -> tuple[str, ...]:
        with self.store.engine.connect() as tx:
            # 常量路径与迁移中的部分索引相同；已完成的大会话不再反复传输/解析。
            return tuple(
                tx.execute(
                    select(models.sessions.c.session_id).where(
                        func.json_extract(
                            models.sessions.c.body, literal_column("'$.requires_replan'")
                        )
                        == 1,
                        func.json_extract(models.sessions.c.body, literal_column("'$.status'"))
                        == literal_column("'ACTIVE'"),
                    )
                ).scalars()
            )

    def recover_pending(self) -> None:
        # 不在 HTTP 准入与执行之间抢走作业；已登记队列仍由空闲扫描恢复。
        if self.stopping.is_set() or self.foreground_requests:
            return
        # HTTP 准入已提交而事实尚未应用时，也必须从持久身份恢复。
        from app.services.request_execution import execute_request
        from app.storage.competition_tasks import HttpRequestRepository

        with self.store.engine.connect() as tx:
            pending = HttpRequestRepository(tx).pending()
            unapplied = {
                record.session_id
                for record in pending
                if RuntimeRepository(tx).event(record.event.event_id.root) is None
            }
        # 已接受事件的等待期先追赶执行事实，再为剩余计划领取求解预算。
        # 尚未应用的准入事件保留原版本，不能被后台时钟抢先越过。
        for session_id in self.clock_sessions():
            if self.stopping.is_set() or self.foreground_requests:
                return
            if session_id not in unapplied:
                with isolated_recovery(self.store, session_id):
                    self.advance_clock(session_id)
        for record in pending:
            if self.stopping.is_set() or self.foreground_requests:
                return
            lock = self.simulation_locks.get(record.session_id)
            if lock is not None and lock.locked():
                continue
            budget = (
                self.policy.initial_budget
                if record.event.event_type == "START_SESSION"
                else self.policy.replan_budget
            )
            limit = Deadline(expires_at_ns=self.clock.monotonic_ns() + budget.total_ms * 1_000_000)
            with isolated_recovery(self.store, record.session_id):
                execute_request(self, record, limit)
        # 先恢复已准入事件，再推进时钟，避免新增时钟事件使待恢复的版本失效。
        for session_id in self.clock_sessions():
            if self.stopping.is_set() or self.foreground_requests:
                return
            with isolated_recovery(self.store, session_id):
                self.advance_clock(session_id)
        for session_id in self.pending_sessions():
            if self.stopping.is_set() or self.foreground_requests:
                return
            lock = self.simulation_locks.get(session_id)
            if lock is not None and lock.locked():
                continue
            with isolated_recovery(self.store, session_id):
                runtime, planner = self.for_session(session_id)
                if runtime.get(session_id).last_planning_failure:
                    continue
                planner.drain(session_id)

    def close(self) -> None:
        self.ready = False
        self.stopping.set()
        self.worker.close()
        if hasattr(self, "store"):
            self.store.close()
