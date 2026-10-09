"""单作业常驻求解进程；父进程拥有截止时间及已验证回退结果。"""

from __future__ import annotations

import logging
import math
import multiprocessing
import os
import queue
import threading
import time
from collections.abc import Callable
from types import TracebackType
from typing import TYPE_CHECKING, Self
from uuid import uuid4

from pydantic import Field

from app.domain.base import FrozenModel, NonEmpty, NonNegativeInt
from app.domain.objectives import ObjectiveStage
from app.domain.ports import Deadline
from app.domain.reports import SolverBuildReport, SolveResult
from app.domain.schedule import CandidateSchedule
from app.domain.scheduling_problem import SchedulingProblem

if TYPE_CHECKING:
    from multiprocessing.process import BaseProcess
    from multiprocessing.queues import Queue

logger = logging.getLogger(__name__)


class _JobArguments(FrozenModel):
    job_id: NonEmpty
    hint: CandidateSchedule | None
    deadline: Deadline
    serial_menu: bool = False
    stage: ObjectiveStage | None = None
    use_cached_problem: bool = Field(default=False, exclude_if=lambda value: value is False)


class WorkerJob(_JobArguments):
    problem: SchedulingProblem


def _attach_problem(problem: SchedulingProblem, arguments: _JobArguments) -> WorkerJob:
    # 内部信封只连接已形成的不可变问题及已核验的本轮参数，不重新复制
    # 整个问题。首条跨进程消息仍由完整 WorkerJob JSON 契约重新核验。
    # 此路径不构造或批准候选，父进程/发布器的独立扫描保持不变。
    return WorkerJob.model_construct(problem=problem, **arguments.__dict__)


class WorkerResponse(FrozenModel):
    job_id: NonEmpty
    ready: bool = False
    worker_pid: NonNegativeInt
    result: SolveResult | None = None
    build_report: SolverBuildReport | None = None


def _worker_loop(inbox: Queue[WorkerJob | str], outbox: Queue[WorkerResponse]) -> None:
    from ortools.sat.python import cp_model

    from app.scheduling.build_report import warmup_build_reporting
    from app.scheduling.cp_sat import CpSatScheduler

    warm = cp_model.CpModel()
    warm.add(True)
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    solver.solve(warm)
    warmup_build_reporting()
    scheduler = CpSatScheduler()
    outbox.put(WorkerResponse(job_id="ready", ready=True, worker_pid=os.getpid()))
    while True:
        job = inbox.get()
        if job == "shutdown":
            return
        if not isinstance(job, WorkerJob):
            raise TypeError("进程收到非法调度消息")
        try:
            result = scheduler.solve(
                job.problem, job.hint, job.deadline, serial_menu=job.serial_menu, stage=job.stage
            )
        except Exception as exc:
            # 异常显式返回失败，不将子进程程序错误伪装成成功计划。
            result = SolveResult(
                status="MODEL_INVALID",
                problem_hash=job.problem.problem_hash,
                diagnostic_message=f"求解进程异常：{type(exc).__name__}: {exc}",
            )
        outbox.put(
            WorkerResponse(
                job_id=job.job_id,
                worker_pid=os.getpid(),
                result=result,
                build_report=scheduler.last_build_report,
            )
        )


class SolverWorker:
    def __init__(
        self,
        *,
        target: Callable[[Queue[WorkerJob | str], Queue[WorkerResponse]], None] | None = None,
        startup_timeout_sec: float = 10,
        result_transport_reserve_sec: float = 0.15,
    ) -> None:
        if not math.isfinite(startup_timeout_sec) or startup_timeout_sec <= 0:
            raise ValueError("求解进程启动等待必须为有限正秒数")
        if not math.isfinite(result_transport_reserve_sec) or result_transport_reserve_sec <= 0:
            raise ValueError("求解结果传输预留必须为有限正秒数")
        self._startup_timeout_ns = int(startup_timeout_sec * 1_000_000_000)
        self._result_transport_reserve_ns = int(result_transport_reserve_sec * 1_000_000_000)
        self._context = multiprocessing.get_context("spawn")
        self._target = target or _worker_loop
        self._process: BaseProcess | None = None
        self._inbox: Queue[WorkerJob | str] | None = None
        self._outbox: Queue[WorkerResponse] | None = None
        self._ready = False
        self._lock = threading.Lock()
        self.last_build_report: SolverBuildReport | None = None
        self.build_reports: list[SolverBuildReport] = []
        self.startup_ms = 0
        self.restart_count = 0
        self._startup_started_ns: int | None = None
        self._estimated_problem_hash: str | None = None
        self._wire_problem_hash: str | None = None
        self._minimum_job_ns = 50_000_000
        self._minimum_cached_job_ns = 50_000_000
        self.cache_problem_messages = False

    @property
    def process_id(self) -> int | None:
        return self._process.pid if self._process is not None else None

    @property
    def is_alive(self) -> bool:
        return self._process is not None and self._process.is_alive()

    @property
    def is_ready(self) -> bool:
        return self.is_alive and self._ready

    def warmup(self, deadline: Deadline | None = None) -> bool:
        # 生命周期预热使用独立启动上限；请求内恢复仍服从调用方的共享截止时间。
        deadline = deadline or Deadline(
            expires_at_ns=time.monotonic_ns() + self._startup_timeout_ns
        )
        if time.monotonic_ns() >= deadline.expires_at_ns:
            return False
        if self.is_alive and self._ready:
            return True
        if not self.is_alive:
            self._abort()
            self._startup_started_ns = time.monotonic_ns()
            self._inbox, self._outbox = self._context.Queue(), self._context.Queue()
            self._process = self._context.Process(
                target=self._target, args=(self._inbox, self._outbox), daemon=True
            )
            self._process.start()
        assert self._startup_started_ns is not None and self._outbox is not None
        started = self._startup_started_ns
        startup_end = started + self._startup_timeout_ns
        cutoff = min(deadline.expires_at_ns, startup_end)
        while time.monotonic_ns() < cutoff:
            if not self.is_alive:
                break
            try:
                response = self._outbox.get(
                    timeout=min(0.02, max(0, cutoff - time.monotonic_ns()) / 1e9)
                )
            except queue.Empty:
                continue
            if response.ready and response.worker_pid == self.process_id:
                self._ready = True
                self.startup_ms = (time.monotonic_ns() - started) // 1_000_000
                return True
        # 此进程尚未接作业，阶段截止不使预热失效。后续阶段共用同一次
        # 有上限的预热；真实作业超时仍由 solve 终止并拒绝过期结果。
        if not self.is_alive or time.monotonic_ns() >= startup_end:
            if self.is_alive:
                logger.error(
                    "求解进程预热超过启动等待上限 %.1f 秒（PID %s），已终止未就绪进程",
                    self._startup_timeout_ns / 1e9,
                    self.process_id,
                )
            else:
                logger.error(
                    "求解进程在就绪前退出（PID %s，exitcode=%s）",
                    self.process_id,
                    self._process.exitcode if self._process is not None else None,
                )
            self._abort()
        return False

    def solve(
        self,
        problem: SchedulingProblem,
        hint: CandidateSchedule | None,
        deadline: Deadline,
        *,
        serial_menu: bool = False,
        stage: ObjectiveStage | None = None,
    ) -> SolveResult:
        identity = problem.problem_hash

        def unknown(message: str) -> SolveResult:
            return SolveResult(status="UNKNOWN", problem_hash=identity, diagnostic_message=message)

        if not self._lock.acquire(blocking=False):
            return unknown("已有调度作业占用唯一求解工作进程")
        try:
            if not self.warmup(deadline):
                return unknown("工作进程未能在共享截止时间前就绪")
            assert self._inbox is not None and self._outbox is not None
            job_id = str(uuid4())
            # 子进程必须预留结果编码与 IPC 时间；父进程期限仍是唯一硬截止时间。
            remaining = max(0, deadline.expires_at_ns - time.monotonic_ns())
            minimum = (
                self._minimum_job_ns if self._estimated_problem_hash == identity else 50_000_000
            )
            use_cache = self.cache_problem_messages and self._wire_problem_hash == identity
            if self._estimated_problem_hash == identity and (
                (stage is not None and stage.name == "E_QUALITY") or (serial_menu and use_cache)
            ):
                # 同一问题的基础模型已在子进程缓存；不能再次收取其完整
                # 构建开销。消息传输和结果返回预留仍计入，实际作业仍受硬期限约束。
                minimum = self._minimum_cached_job_ns
            if remaining < minimum:
                # 无足够的消息传输时间时不派发作业，保留已预热且空闲的进程。
                return unknown("阶段剩余时间不足以完成已测建模和消息传输，未派发作业")
            self.last_build_report = None
            sent_at_ns = time.monotonic_ns()
            child_deadline = Deadline(
                expires_at_ns=deadline.expires_at_ns
                - min(self._result_transport_reserve_ns, remaining // 2)
            )
            self._inbox.put(
                _attach_problem(
                    problem=problem,
                    arguments=_JobArguments(
                        job_id=job_id,
                        hint=hint,
                        deadline=child_deadline,
                        serial_menu=serial_menu,
                        stage=stage,
                        use_cached_problem=use_cache,
                    ),
                )
            )
            while time.monotonic_ns() < deadline.expires_at_ns:
                if not self.is_alive:
                    self._abort()
                    self.restart_count += 1
                    return unknown("求解进程异常退出；未生成可接收结果")
                try:
                    response = self._outbox.get(
                        timeout=min(
                            0.02, max(0, deadline.expires_at_ns - time.monotonic_ns()) / 1e9
                        )
                    )
                except queue.Empty:
                    continue
                if response.job_id != job_id or response.worker_pid != self.process_id:
                    continue
                if time.monotonic_ns() >= deadline.expires_at_ns:
                    break
                result = response.result
                if result is None or result.problem_hash != identity:
                    self._abort()
                    return SolveResult(
                        status="MODEL_INVALID",
                        problem_hash=identity,
                        diagnostic_message="工作进程结果身份不匹配",
                    )
                # 解码成功已替换子进程的输入缓存，即使建模失败、未生成报告。
                # 传输身份与基础模型的计时身份分别记忆，避免 A → 失败 B → A 错引。
                if self.cache_problem_messages:
                    self._wire_problem_hash = identity
                self.last_build_report = response.build_report
                if response.build_report is None and self._estimated_problem_hash != identity:
                    # 无报告不能确认基础模型是否已被 B 替换，不保留 A 的缓存计时优惠。
                    self._estimated_problem_hash = None
                if response.build_report is not None:
                    self.build_reports.append(response.build_report)
                    compute_ns = sum(t.elapsed_ms for t in result.timings) * 1_000_000
                    transport_ns = max(50_000_000, time.monotonic_ns() - sent_at_ns - compute_ns)
                    self._estimated_problem_hash = identity
                    cached_transport = (
                        min(transport_ns, 100_000_000)
                        if self.cache_problem_messages and not use_cache
                        else transport_ns
                    )
                    self._minimum_cached_job_ns = (
                        cached_transport + self._result_transport_reserve_ns
                    )
                    self._minimum_job_ns = (
                        response.build_report.build_time_ms * 1_000_000
                        + transport_ns
                        + self._result_transport_reserve_ns
                    )
                return result
            self._abort()
            self.restart_count += 1
            return unknown("共享截止时间已到，失效进程已终止且过期结果被拒绝")
        finally:
            self._lock.release()

    def _abort(self) -> None:
        process = self._process
        if process is not None:
            if process.is_alive():
                process.terminate()
            process.join(timeout=0.1)
            if process.is_alive():
                process.kill()
                process.join(timeout=0.1)
            if not process.is_alive():
                process.close()
        self._process = None
        self._ready = False
        self._startup_started_ns = None
        self._estimated_problem_hash = None
        self._wire_problem_hash = None
        self._minimum_job_ns = 50_000_000
        self._minimum_cached_job_ns = 50_000_000
        for channel in (self._inbox, self._outbox):
            if channel is not None:
                channel.cancel_join_thread()
                channel.close()
        self._inbox = None
        self._outbox = None

    def close(self) -> None:
        with self._lock:
            if self.is_alive and self._inbox is not None and self._process is not None:
                self._inbox.put("shutdown")
                self._process.join(timeout=0.1)
            self._abort()

    def __enter__(self) -> Self:
        # 生命周期准备在请求之外，最多重试一次；solve 的请求截止时间不重新领取。
        for attempt in range(2):
            if self.warmup():
                return self
            self.close()
            if attempt == 0:
                self.restart_count += 1
        raise TimeoutError("常驻求解进程冷启动失败")

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
