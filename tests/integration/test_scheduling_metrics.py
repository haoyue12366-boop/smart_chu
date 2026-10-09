"""截图真实问题重放，以及真实初排/重排计时与持久化契约。"""

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import ROOT, AppSettings
from app.domain.reports import GreedyResult, SolveResult
from app.domain.schedule import PublishedPlan
from app.domain.scheduling_problem import SchedulingProblem
from app.main import create_app
from app.scheduling.engine import PlanningEngine
from app.scheduling.greedy import GreedyScheduler
from app.scheduling.serial_reference import serial_order_holds
from app.scheduling.tail_compaction import compact_cooking_tails
from app.validation.schedule import ScheduleValidator
from tests.contract.test_competition_endpoint import STEAK
from tests.integration.test_cooking_finish_runtime import new_knowledge
from tests.unit.test_cp_sat_model import deadline

ARCHIVE = ROOT / "data/verification/2026-10-08-scheduling-metrics"


def screenshot_case():
    plan = PublishedPlan.model_validate_json((ARCHIVE / "original-plan.json").read_bytes())
    problem = SchedulingProblem.model_validate_json(
        (ARCHIVE / "original-problem.json").read_bytes()
    )
    return new_knowledge(), problem, plan.validated.candidate


def test_screenshot_serial_reference_keeps_started_cooking_continuations():
    knowledge, problem, _ = screenshot_case()
    original = problem.model_dump_json()
    result = GreedyScheduler().solve_serial(problem, deadline(2))
    assert result.candidate is not None, result
    assert serial_order_holds(result.candidate, problem)
    proof = ScheduleValidator().validate(knowledge, problem.runtime, problem, result.candidate)
    assert proof.valid, proof.violations
    assert problem.model_dump_json() == original


def test_serial_reference_allows_started_reservation_to_release_its_device():
    problem = SchedulingProblem.model_validate_json(
        (ARCHIVE / "browser-extension-failed-problem.json").read_bytes()
    )
    result = GreedyScheduler().solve_serial(problem, deadline(0.4))
    assert result.candidate is not None, result
    assert serial_order_holds(result.candidate, problem)
    assert (
        ScheduleValidator()
        .validate(new_knowledge(), problem.runtime, problem, result.candidate)
        .valid
    )


def test_short_tail_cleanup_budget_prioritizes_the_last_finish(monkeypatch):
    from app.scheduling import tail_compaction

    knowledge, problem, original = screenshot_case()
    limit = deadline(2)
    find = tail_compaction.find_layout_placement
    base = tail_compaction.time.monotonic_ns()
    used = False

    def one_placement(*args, **kwargs):
        nonlocal used
        result = find(*args, **kwargs)
        used = True
        return result

    monkeypatch.setattr(
        tail_compaction.time, "monotonic_ns", lambda: limit.expires_at_ns if used else base
    )
    monkeypatch.setattr(tail_compaction, "find_layout_placement", one_placement)
    compacted = compact_cooking_tails(original, problem, limit)
    assert compacted.metrics.makespan_sec < 9782
    assert compacted.metrics.recipe_cooking_finishes == original.metrics.recipe_cooking_finishes
    assert compacted.metrics.max_continuous_human_sec <= original.metrics.max_continuous_human_sec
    assert ScheduleValidator().validate(knowledge, problem.runtime, problem, compacted).valid


def test_engine_retains_real_reference_when_solver_reference_times_out():
    knowledge, problem, original = screenshot_case()

    class LimitedSolver:
        def solve(self, problem, hint, deadline, **kwargs):
            if kwargs.get("serial_menu"):
                return SolveResult(status="UNKNOWN", problem_hash=problem.problem_hash)
            return SolveResult(
                status="FEASIBLE",
                problem_hash=problem.problem_hash,
                candidate=original,
                objective_stage=kwargs["stage"].name,
            )

    result = PlanningEngine(validator=ScheduleValidator(), solver=LimitedSolver()).plan(
        problem, knowledge, problem.runtime, deadline(5)
    )
    assert result.serial_reference_candidate is not None
    assert serial_order_holds(result.serial_reference_candidate, problem)
    assert (
        result.candidate.metrics.makespan_sec
        <= result.serial_reference_candidate.metrics.makespan_sec
    )


def test_quality_plan_without_serial_reference_cannot_reach_publication(monkeypatch):
    knowledge, problem, original = screenshot_case()

    class UnknownSolver:
        def solve(self, problem, hint, deadline, **kwargs):
            return SolveResult(status="UNKNOWN", problem_hash=problem.problem_hash)

    monkeypatch.setattr(
        GreedyScheduler,
        "solve",
        lambda *args: GreedyResult(status="CANDIDATE_FOUND", candidate=original),
    )
    monkeypatch.setattr(
        GreedyScheduler,
        "solve_serial",
        lambda *args: GreedyResult(status="CONSTRUCTION_FAILED", reason="测试注入：无参考"),
    )
    result = PlanningEngine(validator=ScheduleValidator(), solver=UnknownSolver()).plan(
        problem, knowledge, problem.runtime, deadline(5)
    )
    assert result.status == "FAILED"
    assert result.candidate is None
    assert result.failure.code == "STATE_INCOMPLETE"


def test_plan_and_replan_expose_persisted_system_overhead(tmp_path: Path):
    settings = AppSettings(database_path=tmp_path / "timing.sqlite")
    url = "/api/competition/plan?task_id=timed"
    headers = {"Idempotency-Key": "initial"}
    with TestClient(create_app(settings)) as client:
        initial = client.post(url, json=[STEAK], headers=headers)
        assert initial.status_code == 200, initial.text
        timing = initial.json()["overview"]["planningOverhead"]
        assert timing["kind"] == "INITIAL"
        assert 0 < timing["elapsed_ms"] < 7000
        assert client.post(url, json=[STEAK], headers=headers).text == initial.text
        replan = client.post(
            "/api/competition/replan?task_id=timed", json={}, headers={"Idempotency-Key": "replan"}
        )
        assert replan.status_code == 200, replan.text
        plan = replan.json()["plan"]
        assert plan["planning_overhead"]["kind"] == "REPLAN"
        assert 0 < plan["planning_overhead"]["elapsed_ms"] <= replan.json()["elapsed_ms"]
        sid = plan["session_id"]
        stored = client.get(f"/api/v1/sessions/{sid}/plans/2").json()["plan"]
        assert stored["planning_overhead"] == plan["planning_overhead"]
    with TestClient(create_app(settings)) as restarted:
        assert restarted.post(url, json=[STEAK], headers=headers).text == initial.text


def test_overhead_archive_write_past_deadline_rolls_back_publication(tmp_path, monkeypatch):
    from tests.integration.test_schedule_clock import clock_service, request_replan

    runtime, planner, _ = clock_service(tmp_path)
    serialize = PublishedPlan.model_dump_json
    monotonic = runtime.clock.monotonic_ns
    extra_ns = 0

    def delayed_archive(plan, *args, **kwargs):
        nonlocal extra_ns
        if plan.planning_overhead is not None:
            extra_ns = 10_000_000_000
        return serialize(plan, *args, **kwargs)

    monkeypatch.setattr(PublishedPlan, "model_dump_json", delayed_archive)
    monkeypatch.setattr(runtime.clock, "monotonic_ns", lambda: monotonic() + extra_ns)
    result = request_replan(runtime, planner, 60)
    assert result.status == "FAILED"
    assert runtime.get("clock").runtime.current_plan_version == 1
    runtime.store.close()


def test_unchanged_clock_cursor_does_not_reload_the_large_plan(tmp_path, monkeypatch):
    import pytest

    from app.runtime.schedule_clock import ClockExecutionService
    from app.storage.repositories import RuntimeRepository
    from tests.integration.test_schedule_clock import clock_service, tick
    from tests.runtime_support import event

    runtime, _, _ = clock_service(tmp_path)
    first = tick(runtime, 60)
    original_problem = RuntimeRepository.problem

    def unavailable_plan(*args, **kwargs):
        raise OSError("测试注入：重复读取完整计划失败")

    monkeypatch.setattr(RuntimeRepository, "problem", unavailable_plan)
    assert ClockExecutionService(runtime).advance("clock") == first
    monkeypatch.setattr(RuntimeRepository, "problem", original_problem)
    changed = runtime.apply_event(
        event(first, "changed", "REPLAN_REQUESTED", {"reason": "状态变化"}, at=60)
    )
    assert changed.status == "APPLIED"
    monkeypatch.setattr(RuntimeRepository, "problem", unavailable_plan)
    with pytest.raises(OSError, match="重复读取完整计划失败"):
        ClockExecutionService(runtime).advance("clock")
    runtime.store.close()


def test_clock_rescans_feedback_arriving_after_the_previous_scan(tmp_path, monkeypatch):
    from app.domain.events import EventSource
    from app.runtime.schedule_clock import ClockExecutionService
    from app.runtime.scheduled_actions import PublishedActions
    from tests.integration.test_schedule_clock import clock_service, tick
    from tests.runtime_support import event

    runtime, _, _ = clock_service(tmp_path)
    tick(runtime, 60)
    runtime.clock.advance(61)
    planned = PublishedActions.planned
    injected = False

    def concurrent_feedback(actions, session, cursor):
        nonlocal injected
        result = planned(actions, session, cursor)
        if not injected:
            injected = True
            record = session.runtime.executions[0]
            correction = event(
                session,
                "concurrent-correction",
                "DURATION_UPDATED",
                {
                    "task_id": record.task_ids[0],
                    "execution_id": record.execution_id,
                    "remaining_sec": 0,
                },
                at=61,
            ).model_copy(update={"source": EventSource.MANUAL_CONFIRM})
            assert runtime.apply_event(correction).status == "APPLIED"
        return result

    monkeypatch.setattr(PublishedActions, "planned", concurrent_feedback)
    current = ClockExecutionService(runtime).advance("clock")
    assert current.runtime.executions[0].status == "COMPLETED"
    runtime.store.close()


def test_clock_sync_overhead_does_not_consume_solver_budget(tmp_path, monkeypatch):
    from app.runtime.schedule_clock import ClockExecutionService
    from tests.integration.test_schedule_clock import clock_service, request_replan

    runtime, planner, _ = clock_service(tmp_path)
    advance = ClockExecutionService.advance
    monotonic = runtime.clock.monotonic_ns
    extra_ns = 0

    def slow_sync(*args, **kwargs):
        nonlocal extra_ns
        result = advance(*args, **kwargs)
        extra_ns += 1_500_000_000
        return result

    monkeypatch.setattr(runtime.clock, "monotonic_ns", lambda: monotonic() + extra_ns)
    monkeypatch.setattr(ClockExecutionService, "advance", slow_sync)
    result = request_replan(runtime, planner, 60)
    assert result.status == "PUBLISHED", result
    assert result.plan.planning_overhead.elapsed_ms >= 3000
    assert result.elapsed_ms >= 3000
    runtime.store.close()


def test_compilation_overhead_does_not_consume_solver_budget(tmp_path, monkeypatch):
    from app.compiler.compiler import ProblemCompiler
    from tests.integration.test_schedule_clock import clock_service, request_replan

    runtime, planner, _ = clock_service(tmp_path)
    compile_problem = ProblemCompiler.compile
    monotonic = runtime.clock.monotonic_ns
    extra_ns = 0

    def slow_compile(*args, **kwargs):
        nonlocal extra_ns
        result = compile_problem(*args, **kwargs)
        extra_ns += 2_000_000_000
        return result

    monkeypatch.setattr(runtime.clock, "monotonic_ns", lambda: monotonic() + extra_ns)
    monkeypatch.setattr(ProblemCompiler, "compile", slow_compile)
    result = request_replan(runtime, planner, 60)
    assert result.status == "PUBLISHED", result
    assert result.plan.planning_overhead.elapsed_ms >= 2000
    assert result.elapsed_ms >= 2000
    runtime.store.close()


def test_preparation_phase_still_rejects_its_own_deadline():
    import pytest

    from app.services.preparation_budget import PreparationBudget

    now = 1_000_000_000
    preparation = PreparationBudget(lambda: now)
    with pytest.raises(TimeoutError, match="准备阶段"):
        with preparation.measure(2400):
            now += 10_000_000_000


def test_workbench_event_admission_excludes_clock_sync_but_counts_event_work(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from fastapi import FastAPI

    from app.api.events import router
    from app.runtime.schedule_clock import ClockExecutionService
    from tests.integration.test_schedule_clock import clock_service, tick

    runtime, planner, _ = clock_service(tmp_path)
    current = tick(runtime, 60)
    monotonic = runtime.clock.monotonic_ns
    extra_ns = 0
    applied = False
    apply_event = runtime.apply_event

    def advance_clock(sid, deadline=None):
        nonlocal extra_ns
        result = ClockExecutionService(runtime).advance(sid, deadline=deadline)
        extra_ns += 1_800_000_000
        return result

    def ordinary_work(*args, **kwargs):
        nonlocal extra_ns, applied
        if not applied:
            applied = True
            extra_ns += 700_000_000
        return apply_event(*args, **kwargs)

    monkeypatch.setattr(runtime.clock, "monotonic_ns", lambda: monotonic() + extra_ns)
    monkeypatch.setattr(runtime, "apply_event", ordinary_work)
    app = FastAPI()
    app.include_router(router)
    app.state.container = SimpleNamespace(
        clock=runtime.clock,
        store=runtime.store,
        policy=current.policy,
        request_locks={},
        for_session=lambda sid: (runtime, planner),
        advance_clock=advance_clock,
    )

    @app.middleware("http")
    async def request_start(request, call_next):
        request.state.started_ns = runtime.clock.monotonic_ns()
        return await call_next(request)

    with TestClient(app) as client:
        result = client.post(
            "/api/v1/sessions/clock/events",
            json={
                "event_id": "workbench-budget",
                "event_type": "ADD_RECIPE",
                "expected_state_revision": current.runtime.state_revision,
                "base_plan_version": current.runtime.current_plan_version,
                "source": "SCHEDULE_CLOCK",
                "payload": {"recipes": [{"id": "clock-1", "name": "合成时钟工艺1"}]},
            },
        )
        assert result.status_code == 200, result.text
        assert result.json()["status"] == "PUBLISHED", result.text
        assert result.json()["plan"]["planning_overhead"]["elapsed_ms"] >= 2500
    runtime.store.close()


def test_cheesecake_addition_extends_previous_parallel_plan_with_real_saving():
    previous = PublishedPlan.model_validate_json(
        (ARCHIVE / "browser-initial-plan.json").read_bytes()
    )
    problem = SchedulingProblem.model_validate_json(
        (ARCHIVE / "browser-extension-problem.json").read_bytes()
    )
    result = GreedyScheduler().solve_extension(problem, previous.validated.candidate, deadline(0.4))
    assert result.candidate is not None, result
    assert result.candidate.metrics.makespan_sec < 394 * 60
    assert (
        ScheduleValidator()
        .validate(new_knowledge(), problem.runtime, problem, result.candidate)
        .valid
    )
