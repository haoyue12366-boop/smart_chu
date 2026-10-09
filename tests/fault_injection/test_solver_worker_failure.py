"""显式合成故障进程：异常退出、挂起与父进程的合法回退。"""

import os
import time

import pytest

from app.domain.policy import ModelSize
from app.domain.reports import SolverBuildReport, SolveResult
from app.scheduling.engine import PlanningEngine
from app.scheduling.worker import SolverWorker, WorkerResponse
from app.validation.schedule import ScheduleValidator
from tests.unit.test_cp_sat_model import deadline
from tests.unit.test_schedule_validator import example


def crash_worker(inbox, outbox):
    outbox.put(WorkerResponse(job_id="ready", ready=True, worker_pid=os.getpid()))
    inbox.get()
    os._exit(23)


def hung_worker(inbox, outbox):
    outbox.put(WorkerResponse(job_id="ready", ready=True, worker_pid=os.getpid()))
    inbox.get()
    time.sleep(10)


def wrong_report_worker(inbox, outbox):
    outbox.put(WorkerResponse(job_id="ready", ready=True, worker_pid=os.getpid()))
    job = inbox.get()
    report = SolverBuildReport(
        problem_hash="0" * 64 if job.serial_menu else job.problem.problem_hash,
        solver_build_id="synthetic-report",
        objective_stage="SYNTHETIC",
        solver_version="synthetic",
        actual_model_size=ModelSize(),
    )
    outbox.put(
        WorkerResponse(
            job_id=job.job_id,
            worker_pid=os.getpid(),
            result=SolveResult(
                status="UNKNOWN",
                problem_hash=job.problem.problem_hash,
                build_report_ref="synthetic-report" if job.serial_menu else "wrong-reference",
            ),
            build_report=report,
        )
    )
    inbox.get()


@pytest.mark.parametrize("wrong_problem", [True, False])
def test_unbound_build_report_is_rejected_before_archiving(wrong_problem):
    _, _, problem, _ = example()
    archived = []
    with SolverWorker(target=wrong_report_worker, report_sink=archived.append) as worker:
        result = worker.solve(problem, None, deadline(5), serial_menu=wrong_problem)
        assert result.status == "MODEL_INVALID"
        assert "报告身份" in result.diagnostic_message
        assert not archived and not worker.build_reports
        assert not worker.is_alive


def quick_report_worker(inbox, outbox):
    outbox.put(WorkerResponse(job_id="ready", ready=True, worker_pid=os.getpid()))
    job = inbox.get()
    outbox.put(
        WorkerResponse(
            job_id=job.job_id,
            worker_pid=os.getpid(),
            result=SolveResult(status="UNKNOWN", problem_hash=job.problem.problem_hash),
            build_report=SolverBuildReport(
                problem_hash=job.problem.problem_hash,
                solver_build_id="synthetic-fast-report",
                objective_stage="SYNTHETIC",
                solver_version="synthetic",
                actual_model_size=ModelSize(),
            ),
        )
    )
    inbox.get()


def test_slow_archive_still_consumes_shared_deadline_without_killing_idle_worker():
    _, _, problem, _ = example()
    archived = []

    def slow(report):
        time.sleep(1.1)
        archived.append(report)

    with SolverWorker(target=quick_report_worker, report_sink=slow) as worker:
        pid = worker.process_id
        result = worker.solve(problem, None, deadline(1))
        assert result.status == "UNKNOWN" and result.candidate is None
        assert "结果处理" in result.diagnostic_message
        assert len(archived) == 1
        assert worker.process_id == pid and worker.is_ready
        assert worker.restart_count == 0


def slow_ready_worker(inbox, outbox):
    time.sleep(0.2)
    deadline_respecting_worker(inbox, outbox)


def test_short_stage_does_not_repeatedly_discard_unassigned_starting_worker():
    worker = SolverWorker(target=slow_ready_worker)
    try:
        assert not worker.warmup(deadline(0.01))
        pid = worker.process_id
        assert pid is not None and worker.is_alive
        assert not worker.warmup(deadline(0.01))
        assert worker.process_id == pid
        assert worker.warmup(deadline(5))
        assert worker.process_id == pid
        assert worker.restart_count == 0
    finally:
        worker.close()


def deadline_respecting_worker(inbox, outbox):
    outbox.put(WorkerResponse(job_id="ready", ready=True, worker_pid=os.getpid()))
    job = inbox.get()
    while time.monotonic_ns() < job.deadline.expires_at_ns:
        time.sleep(0.001)
    outbox.put(
        WorkerResponse(
            job_id=job.job_id,
            worker_pid=os.getpid(),
            result=SolveResult(status="UNKNOWN", problem_hash=job.problem.problem_hash),
        )
    )
    inbox.get()


def slow_response_worker(inbox, outbox):
    outbox.put(WorkerResponse(job_id="ready", ready=True, worker_pid=os.getpid()))
    job = inbox.get()
    while time.monotonic_ns() < job.deadline.expires_at_ns:
        time.sleep(0.001)
    # 合成低算力上的结果序列化/传输延迟，不提供伪造可行计划。
    time.sleep(0.3)
    outbox.put(
        WorkerResponse(
            job_id=job.job_id,
            worker_pid=os.getpid(),
            result=SolveResult(status="UNKNOWN", problem_hash=job.problem.problem_hash),
        )
    )
    inbox.get()


def test_cloud_result_transport_reserve_keeps_worker_ready():
    _, _, problem, _ = example()
    with SolverWorker(target=slow_response_worker, result_transport_reserve_sec=0.6) as worker:
        pid = worker.process_id
        result = worker.solve(problem, None, deadline(1.2))
        assert result.status == "UNKNOWN" and result.candidate is None
        assert worker.is_alive and worker.process_id == pid
        assert worker.restart_count == 0


def test_cooperative_timeout_reserves_ipc_and_keeps_warm_worker():
    _, _, problem, _ = example()
    with SolverWorker(target=deadline_respecting_worker) as worker:
        pid = worker.process_id
        result = worker.solve(problem, None, deadline(0.3))
        assert result.status == "UNKNOWN"
        assert worker.is_alive
        assert worker.process_id == pid
        assert worker.restart_count == 0


def test_phase_too_short_for_transport_keeps_unassigned_warm_worker():
    _, _, problem, _ = example()
    with SolverWorker() as worker:
        pid = worker.process_id
        result = worker.solve(problem, None, deadline(0.01))
        assert result.status == "UNKNOWN"
        assert result.candidate is None
        assert worker.is_alive
        assert worker.process_id == pid
        assert worker.restart_count == 0


def test_crashed_worker_cannot_change_runtime_and_parent_keeps_validated_candidate():
    knowledge, state, problem, _ = example()
    before = state.model_dump_json()
    with SolverWorker(target=crash_worker) as worker:
        result = PlanningEngine(validator=ScheduleValidator(), solver=worker).plan(
            problem, knowledge, state, deadline()
        )
        assert result.status == "VALIDATED", result
        assert result.validation.valid
        assert before == state.model_dump_json()
    assert not worker.is_alive


def test_hung_process_is_killed_and_no_partial_candidate_returned():
    _, _, problem, _ = example()
    with SolverWorker(target=hung_worker) as worker:
        started = time.monotonic()
        result = worker.solve(problem, None, deadline(0.15))
        elapsed = time.monotonic() - started
        assert result.status == "UNKNOWN"
        assert result.candidate is None
        assert elapsed < 0.8
        assert not worker.is_alive
