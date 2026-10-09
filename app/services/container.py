"""生命周期持有版本化知识、状态库和唯一求解工作进程。"""

import json
import logging
from _thread import LockType
from datetime import datetime
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
from app.services.deployment_policy import deployment_policy
from app.services.planning import PlanningService
from app.services.recovery_guard import isolated_recovery
from app.services.resource_monitor import ResourceMonitor
from app.storage import models
from app.storage.repositories import RuntimeRepository
from app.storage.solver_reports import SolverReportArchive
from app.storage.unit_of_work import UnitOfWork

logger = logging.getLogger(__name__)


class ServiceContainer:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self._resource_monitor = ResourceMonitor()
        self.clock = SystemClock()
        self.repository = SnapshotKnowledgeRepository(settings.release_root)
        self.worker = JsonSolverWorker(
            startup_timeout_sec=settings.solver_startup_timeout_sec,
            result_transport_reserve_sec=3 if settings.planning_profile == "RENDER" else 0.15,
            report_sink=SolverReportArchive(settings.database_path.parent / "solver-build-reports"),
            max_retained_build_reports=1,
        )
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
        self.policy = deployment_policy(
            SchedulingPolicy.model_validate_json(
                self.settings.policy_path.read_text(encoding="utf-8")
            ),
            self.settings.planning_profile,
        )
        logger.info(
            "排程配置 %s，策略 %s，初排/重排预算 %s/%s ms，求解线程 %s",
            self.settings.planning_profile,
            self.policy.policy_version,
            self.policy.initial_budget.total_ms,
            self.policy.replan_budget.total_ms,
            self.policy.max_solver_search_workers,
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
            del lease
            runtime = RuntimeService(self.store, knowledge, self.clock)
            self.runtimes[release_id] = runtime
            self.planners[release_id] = PlanningService(
                runtime,
                self.worker,
                job_lock=self.job_lock,
                on_compute_finished=self.worker.release_problem,
            )
        self.knowledge = self.runtimes[self.settings.release_id].knowledge
        # Runtime持有经严格构造的完整视图；原快照物化缓存已不在在线读取路径。
        self.repository.trim_unused_cache()
        if not self.worker.warmup():
            raise TimeoutError(
                "求解进程未就绪"
                f"（启动等待上限 {self.settings.solver_startup_timeout_sec:g} 秒，"
                "详见前面的预热超时或子进程退出日志）"
            )
        self.ready = True

    @property
    def runtime(self) -> RuntimeService:
        return self.runtimes[self.settings.release_id]

    def resources_snapshot(self) -> dict[str, object]:
        result = self._resource_monitor.read(
            worker_process_id=self.worker.process_id if self.worker.is_alive else None
        )
        result.update(
            solver_ready=self.worker.is_ready,
            solver_restarts=self.worker.restart_count,
            retained_build_reports=len(self.worker.build_reports),
            cache_release_requests=self.worker.cache_release_requests,
            cache_release_failures=self.worker.cache_release_failures,
        )
        return result

    @property
    def planner(self) -> PlanningService:
        return self.planners[self.settings.release_id]

    def for_session(self, session_id: str) -> tuple[RuntimeService, PlanningService]:
        with self.store.engine.connect() as tx:
            # 选择运行时不需要反序列化整桌工序、物料、策略及历史。
            # 绑定和模拟偏移仍从当前数据库读取，不缓存可变状态。
            row = tx.execute(
                select(
                    func.json_extract(models.sessions.c.body, "$.knowledge_release_id").label(
                        "release_id"
                    ),
                    func.json_extract(models.sessions.c.body, "$.runtime.execution_mode").label(
                        "mode"
                    ),
                    func.json_extract(models.sessions.c.body, "$.runtime.now_offset_sec").label(
                        "offset"
                    ),
                    func.json_extract(
                        models.sessions.c.body, "$.runtime.time_origin.start_at"
                    ).label("origin"),
                ).where(models.sessions.c.session_id == session_id)
            ).first()
        if row is None:
            raise KeyError("会话不存在：" + session_id)
        release_id = row.release_id
        if release_id is None or release_id not in self.runtimes:
            raise ValueError("会话绑定的知识版本尚未加载")
        if row.mode == "SIMULATED":
            if session_id not in self.simulated_sessions:
                clock = SimulationClock(datetime.fromisoformat(row.origin))
                runtime = RuntimeService(self.store, self.runtimes[release_id].knowledge, clock)
                planner = PlanningService(
                    runtime,
                    self.worker,
                    job_lock=self.job_lock,
                    on_compute_finished=self.worker.release_problem,
                )
                self.simulated_sessions[session_id] = runtime, planner
            runtime, planner = self.simulated_sessions[session_id]
            assert isinstance(runtime.clock, SimulationClock)
            runtime.clock.advance(max(runtime.clock.offset_sec, row.offset))
            return runtime, planner
        return self.runtimes[release_id], self.planners[release_id]

    def knowledge_for(self, session_id: str) -> MenuKnowledgeView:
        return self.for_session(session_id)[0].knowledge

    def advance_clock(
        self,
        session_id: str,
        deadline: Deadline | None = None,
        *,
        persist_idle_progress: bool = True,
    ) -> RuntimeSession:
        """在事件准入前或后台同步已流逝的时钟，串行处理同一会话的边界。"""
        with self.clock_locks.setdefault(session_id, Lock()):
            runtime, _ = self.for_session(session_id)
            return ClockExecutionService(runtime).advance(
                session_id,
                deadline=deadline,
                persist_idle_progress=persist_idle_progress,
            )

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
        if not self.worker.is_ready and self.job_lock.acquire(blocking=False):
            try:
                # 异常退出或有界作业超时后，在空闲轮询中渐进预热；不依赖下一次请求。
                self.worker.warmup(Deadline(expires_at_ns=self.clock.monotonic_ns() + 500_000_000))
            finally:
                self.job_lock.release()
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
                    self.advance_clock(session_id, persist_idle_progress=False)
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
                self.advance_clock(session_id, persist_idle_progress=False)
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
