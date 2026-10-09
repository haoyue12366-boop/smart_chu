"""固定 P4 授权发布的 H02 规则；动态反馈为明确标注的模拟事件。"""

import time

import pytest

from app.compiler.compiler import ProblemCompiler
from app.domain.candidates import stable_id
from app.domain.ports import Deadline
from app.domain.schedule import PublishContext, ValidatedSchedule
from app.domain.scheduling_problem import SchedulingProblem
from app.runtime.replanning import prepare_replan
from app.runtime.simulated_materials import simulated_payload
from app.runtime.simulator import Simulator
from app.scheduling.cp_sat import CpSatScheduler
from app.scheduling.greedy import GreedyScheduler
from app.scheduling.worker import SolverWorker
from app.services.publishing import PlanPublisher
from app.storage.repositories import RuntimeRepository
from app.validation.schedule import ScheduleValidator
from tests.integration.test_menu_events_replanning import service
from tests.runtime_support import event, p4_knowledge


@pytest.fixture(scope="module")
def thermal_worker():
    with SolverWorker() as worker:
        yield worker


def running_h02(tmp_path, solver):
    knowledge = p4_knowledge()
    identities = {"5c8200d96dc6e123a037a202", "5fe197175f8f38795ea6fe77"}
    recipes = [r for r in knowledge.recipes if r.recipe_id.root in identities]
    runtime, planning, session = service(tmp_path, solver=solver, knowledge=knowledge)
    result = runtime.apply_event(
        event(
            session,
            "h02-start",
            "START_SESSION",
            {
                "recipes": [{"id": r.recipe_id, "name": r.name} for r in recipes],
            },
        )
    )
    assert result.status == "APPLIED", result
    current = runtime.get("flow")
    # 此处离线准备确定的 H02 初始场景；优化器限时选择单菜也合法，不能借此跳过批次验收。
    # 初排性能单独测量。下面所有服务重排仍使用固定 2400 ms、Greedy 100 ms 和 CP 1500 ms。
    limit = Deadline(expires_at_ns=time.monotonic_ns() + 2_000_000_000)
    problem = ProblemCompiler().compile(
        knowledge, current.menu, current.runtime, current.policy, limit
    )
    assert isinstance(problem, SchedulingProblem), problem
    solved = GreedyScheduler().solve(problem, limit)
    assert solved.candidate is not None, solved
    proof = ScheduleValidator().validate(knowledge, current.runtime, problem, solved.candidate)
    assert proof.valid, proof
    published = PlanPublisher(runtime.store, knowledge, runtime.clock, problem).publish(
        ValidatedSchedule(candidate=solved.candidate, validation=proof),
        PublishContext(
            session_id=current.runtime.session_id,
            base_state_revision=current.runtime.state_revision,
            base_plan_version=current.runtime.current_plan_version,
            knowledge_version=current.runtime.knowledge_version,
            snapshot_id=current.runtime.snapshot_id,
            request_id="h02-offline-fixture",
            publication_id="h02-offline-fixture",
        ),
    )
    assert published.plan_version == 1
    current = runtime.get("flow")
    binding = next(b for b in current.bindings if b.carrier.kind == "THERMAL_BATCH")
    heat_ids = {
        stable_id("task", instance.recipe_instance_id.root, operation.operation_id.root)
        for instance in current.menu
        for recipe in recipes
        if instance.recipe_id == recipe.recipe_id
        for operation in recipe.operations
        if operation.action == "HEAT"
    }
    heats = tuple(span for span in binding.task_spans if span.task_id.root in heat_ids)
    assert len(heats) == 2 and len({span.interval for span in heats}) == 1
    simulator = Simulator(runtime, "flow", seed=53)
    simulator.advance(heats[0].interval.start_sec + 30)
    current = runtime.get("flow")
    record = next(
        e for e in current.runtime.executions if e.carrier_id == binding.carrier.carrier_id.root
    )
    assert record.status == "RUNNING"
    assert all(span.task_id in record.started_task_ids for span in heats)
    return runtime, planning, simulator, binding, record, heats


def test_h02_running_batch_keeps_members_when_new_recipe_is_added(tmp_path, thermal_worker):
    runtime, planning, simulator, binding, running, _ = running_h02(tmp_path, thermal_worker)
    current = runtime.get("flow")
    recipe = next(r for r in runtime.knowledge.recipes if r.recipe_id == current.menu[0].recipe_id)
    result = planning.apply_event(
        event(
            current,
            "h02-add",
            "ADD_RECIPE",
            {
                "recipes": [{"id": recipe.recipe_id, "name": recipe.name}],
            },
            at=runtime.clock.offset_sec,
        )
    )
    assert result.status == "PUBLISHED", result
    current = runtime.get("flow")
    assert (
        next(e for e in current.runtime.executions if e.execution_id == running.execution_id)
        == running
    )
    assert len(result.plan.validated.candidate.recipe_completions) == 3
    with runtime.store.engine.connect() as tx:
        problem = RuntimeRepository(tx).problem("flow", result.plan.plan_version)
    frozen = next(e for e in problem.fixed_executions if e.execution_id == running.execution_id)
    assert frozen.task_ids == running.task_ids
    assert frozen.task_spans == running.task_spans
    assert frozen.remaining_sec == binding.assignment.interval.end_sec - runtime.clock.offset_sec
    for algorithm in (GreedyScheduler(), CpSatScheduler()):
        limit = Deadline(expires_at_ns=time.monotonic_ns() + 2_000_000_000)
        solved = (
            algorithm.solve(problem, limit)
            if isinstance(algorithm, GreedyScheduler)
            else algorithm.solve(problem, None, limit)
        )
        assert solved.candidate is not None, solved
        report = ScheduleValidator().validate(
            runtime.knowledge, problem.runtime, problem, solved.candidate
        )
        assert report.valid, report
        assert not any(
            set(a.task_ids).intersection(running.task_ids) for a in solved.candidate.assignments
        )
    simulator.advance(result.plan.validated.candidate.metrics.makespan_sec)
    after = runtime.get("flow")
    old = next(e for e in after.runtime.executions if e.execution_id == running.execution_id)
    assert old.status == "COMPLETED" and old.task_ids == running.task_ids


def test_cancel_one_running_member_preserves_batch_until_confirmed_end(tmp_path, thermal_worker):
    runtime, planning, simulator, binding, running, _ = running_h02(tmp_path, thermal_worker)
    current = runtime.get("flow")
    result = planning.apply_event(
        event(
            current,
            "h02-cancel",
            "CANCEL_RECIPE",
            {
                "recipe_instance_id": current.menu[0].recipe_instance_id,
            },
            at=runtime.clock.offset_sec,
        )
    )
    assert result.event.status == "APPLIED", result
    current = runtime.get("flow")
    assert (
        next(e for e in current.runtime.executions if e.execution_id == running.execution_id)
        == running
    )
    assert any(
        o.execution_id == running.execution_id and o.released_at is None
        for o in current.runtime.details.occupancies
    )
    simulator.advance(binding.assignment.interval.end_sec)
    finished = runtime.get("flow")
    old = next(e for e in finished.runtime.executions if e.execution_id == running.execution_id)
    assert old.status == "COMPLETED"
    assert old.task_ids == running.task_ids
    assert not any(
        o.execution_id == old.execution_id and o.released_at is None
        for o in finished.runtime.details.occupancies
    )
    replanned = planning.drain("flow")
    # 取消事件若已发布剩余菜单，按预测完成无需再次求解；失败后仍须恢复有效发布。
    assert replanned.status in {"PUBLISHED", "NO_REPLAN"}, replanned
    assert len(replanned.plan.validated.candidate.recipe_completions) == 1


def test_late_internal_start_records_actual_stage_and_human_boundary(tmp_path, thermal_worker):
    runtime, _, _, binding, running, heats = running_h02(tmp_path, thermal_worker)
    with runtime.store.engine.connect() as tx:
        problem = RuntimeRepository(tx).problem("flow", binding.plan_version)
    current = runtime.get("flow")
    heat_end = heats[0].interval.end_sec
    heat_group = tuple(span.task_id for span in heats)
    payload = simulated_payload(
        current, problem, heat_group, running.execution_id.root, completed=True
    )
    assert (
        runtime.apply_event(
            event(current, "heat-finish", "OPERATION_COMPLETED", payload, at=heat_end)
        ).status
        == "APPLIED"
    )
    next_span = min(
        (p for p in binding.task_spans if p.interval.start_sec >= heat_end),
        key=lambda p: p.interval.start_sec,
    )
    group = tuple(p.task_id for p in binding.task_spans if p.interval == next_span.interval)
    actual_start = next_span.interval.start_sec + 30
    current = runtime.get("flow")
    payload = simulated_payload(current, problem, group, running.execution_id.root, completed=False)
    started = runtime.apply_event(
        event(current, "late-internal-start", "OPERATION_STARTED", payload, at=actual_start)
    )
    assert started.status == "APPLIED", started
    current = runtime.get("flow")
    updated = next(e for e in current.runtime.executions if e.execution_id == running.execution_id)
    assert updated.started_at == running.started_at
    assert all(
        p.interval.start_sec == actual_start for p in updated.task_spans if p.task_id in group
    )
    active = [
        o
        for o in current.runtime.details.occupancies
        if o.execution_id == updated.execution_id
        and o.resource.resource_type == "HUMAN"
        and o.released_at is None
    ]
    assert len(active) == 1 and active[0].started_at == current.runtime.time_origin.at(actual_start)


def test_internal_duration_ages_current_heat_and_preserves_completed_stages(
    tmp_path, thermal_worker
):
    runtime, _, _, binding, running, heats = running_h02(tmp_path, thermal_worker)
    current = runtime.get("flow")
    observed = runtime.clock.offset_sec
    phase_remaining = heats[0].interval.end_sec - observed
    unchanged = runtime.apply_event(
        event(
            current,
            "same-heat-duration",
            "DURATION_UPDATED",
            {
                "task_id": heats[0].task_id,
                "execution_id": running.execution_id,
                "remaining_sec": phase_remaining,
            },
            at=observed,
        )
    )
    assert unchanged.status == "APPLIED" and not unchanged.requires_replan
    current = runtime.get("flow")
    changed = runtime.apply_event(
        event(
            current,
            "extend-heat-duration",
            "DURATION_UPDATED",
            {
                "task_id": heats[0].task_id,
                "execution_id": running.execution_id,
                "remaining_sec": phase_remaining + 240,
            },
            at=observed,
        )
    )
    assert changed.status == "APPLIED" and changed.requires_replan
    current = runtime.get("flow")
    after = next(e for e in current.runtime.executions if e.execution_id == running.execution_id)
    assert after.started_at == running.started_at
    assert tuple(p for p in after.task_spans if p.task_id in after.completed_task_ids) == tuple(
        p for p in running.task_spans if p.task_id in running.completed_task_ids
    )
    assert after.remaining_sec == binding.assignment.interval.end_sec + 240 - observed
    assert all(
        span.interval.end_sec == heats[0].interval.end_sec + 240
        for span in after.task_spans
        if span.task_id in {p.task_id for p in heats}
    )
    runtime.clock.advance(observed + 60)
    current = runtime.get("flow")
    # 模拟业务时间和真实观测起点分离，投影不得改写持久化的观测值。
    projected_session = current.model_copy(
        update={"runtime": current.runtime.model_copy(update={"now_offset_sec": observed + 60})}
    )
    request = prepare_replan(projected_session, changed, runtime.knowledge, current.policy)
    projected = next(e for e in request.runtime.executions if e.execution_id == after.execution_id)
    assert projected.remaining_sec == after.remaining_sec - 60
    assert projected.task_spans == after.task_spans
    assert runtime.get("flow").runtime.executions == current.runtime.executions


def test_overdue_current_heat_cannot_be_hidden_by_unstarted_batch_tail(tmp_path, thermal_worker):
    runtime, planning, _, _, running, heats = running_h02(tmp_path, thermal_worker)
    at = heats[0].interval.end_sec + 1
    assert at < max(span.interval.end_sec for span in running.task_spans)
    runtime.clock.advance(at)
    current = runtime.get("flow")
    recipe = next(
        item
        for item in runtime.knowledge.recipes
        if item.recipe_id.root == "5c8200d96dc6e123a037a202"
    )
    changed = planning.apply_event(
        event(
            current,
            "overdue-heat-add",
            "ADD_RECIPE",
            {"recipes": [{"id": recipe.recipe_id, "name": recipe.name}]},
            at=at,
        )
    )
    assert changed.event.status == "APPLIED" and changed.status == "FAILED", changed
    assert changed.planning.failure.code == "STATE_INCOMPLETE"
    after = runtime.get("flow")
    assert after.dispatch_blocked
    assert after.runtime.executions == current.runtime.executions
    assert all(
        item.released_at is None
        for item in after.runtime.details.occupancies
        if item.execution_id == running.execution_id and item.resource.resource_type == "DEVICE"
    )


def test_unstarted_past_internal_phase_requires_actual_start_before_replan(
    tmp_path, thermal_worker
):
    runtime, planning, _, binding, running, heats = running_h02(tmp_path, thermal_worker)
    with runtime.store.engine.connect() as tx:
        problem = RuntimeRepository(tx).problem("flow", binding.plan_version)
    current = runtime.get("flow")
    heat_end = heats[0].interval.end_sec
    heat_group = tuple(span.task_id for span in heats)
    assert (
        runtime.apply_event(
            event(
                current,
                "heat-confirmed",
                "OPERATION_COMPLETED",
                simulated_payload(
                    current, problem, heat_group, running.execution_id.root, completed=True
                ),
                at=heat_end,
            )
        ).status
        == "APPLIED"
    )
    next_span = min(
        (span for span in binding.task_spans if span.interval.start_sec >= heat_end),
        key=lambda span: span.interval.start_sec,
    )
    group = tuple(
        span.task_id for span in binding.task_spans if span.interval == next_span.interval
    )
    at = next_span.interval.start_sec + 30
    runtime.clock.advance(at)
    current = runtime.get("flow")
    before = next(
        item for item in current.runtime.executions if item.execution_id == running.execution_id
    )
    recipe = next(
        item
        for item in runtime.knowledge.recipes
        if item.recipe_id.root == "5c8200d96dc6e123a037a202"
    )
    changed = planning.apply_event(
        event(
            current,
            "unconfirmed-phase-add",
            "ADD_RECIPE",
            {"recipes": [{"id": recipe.recipe_id, "name": recipe.name}]},
            at=at,
        )
    )
    assert changed.event.status == "APPLIED" and changed.status == "FAILED", changed
    assert changed.planning.failure.code == "STATE_INCOMPLETE"
    failed = runtime.get("flow")
    assert failed.runtime.executions == current.runtime.executions
    started = runtime.apply_event(
        event(
            failed,
            "actual-late-phase-start",
            "OPERATION_STARTED",
            simulated_payload(failed, problem, group, running.execution_id.root, completed=False),
            at=at,
        )
    )
    assert started.status == "APPLIED", started
    recovered = planning.drain("flow")
    assert recovered.status == "PUBLISHED", recovered.model_dump_json()
    updated = next(
        item
        for item in runtime.get("flow").runtime.executions
        if item.execution_id == running.execution_id
    )
    assert all(
        span.interval.start_sec == at for span in updated.task_spans if span.task_id in group
    )
    assert tuple(
        span for span in updated.task_spans if span.task_id in before.completed_task_ids
    ) == tuple(span for span in before.task_spans if span.task_id in before.completed_task_ids)


def test_independent_validator_rejects_overdue_internal_phase_without_runtime_projection(
    tmp_path, thermal_worker
):
    runtime, _, _, _, running, heats = running_h02(tmp_path, thermal_worker)
    session = runtime.get("flow")
    at = heats[0].interval.end_sec + 1
    observed = session.runtime.time_origin.offset(running.remaining_observed_at)
    stale_progress = running.model_copy(
        update={"remaining_sec": running.remaining_sec - at + observed}
    )
    assert stale_progress.remaining_sec > 0
    snapshot = session.runtime.model_copy(
        update={
            "now_offset_sec": at,
            "executions": tuple(
                stale_progress if item.execution_id == running.execution_id else item
                for item in session.runtime.executions
            ),
        }
    )
    # 故意绕过 Runtime 投影，直接交给 Compiler 和 Greedy。
    problem = ProblemCompiler().compile(
        runtime.knowledge,
        session.menu,
        snapshot,
        session.policy,
        Deadline(expires_at_ns=time.monotonic_ns() + 2_000_000_000),
    )
    assert isinstance(problem, SchedulingProblem), problem
    candidate = (
        GreedyScheduler()
        .solve(problem, Deadline(expires_at_ns=time.monotonic_ns() + 2_000_000_000))
        .candidate
    )
    assert candidate is not None
    proof = ScheduleValidator().validate(runtime.knowledge, snapshot, problem, candidate)
    assert any(item.code == "HISTORY_PROGRESS" for item in proof.violations), proof
