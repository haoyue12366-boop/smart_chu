"""明确合成的装入、等待、取出三阶段；同一灶眼必须连续保留。"""

import time

import pytest

from app.compiler.compiler import ProblemCompiler
from app.domain.candidates import stable_id
from app.domain.canonical_recipe import CanonicalRecipeModel
from app.domain.ports import Deadline
from app.domain.recipe_context import RecipeReservation, RecipeSchedulingContext
from app.domain.scheduling_problem import SchedulingProblem
from app.runtime.recovery import restore_session
from app.runtime.replanning import prepare_replan
from app.runtime.simulator import Simulator
from app.scheduling.cp_sat import CpSatScheduler
from app.scheduling.greedy import GreedyScheduler
from app.storage.unit_of_work import UnitOfWork
from app.validation.schedule import ScheduleValidator
from tests.integration.test_menu_events_replanning import service
from tests.runtime_support import event, p4_knowledge


def continuous_service(tmp_path, *, choice=False, compatible=False):
    knowledge = p4_knowledge()
    use = (
        next(
            use
            for recipe in knowledge.recipes
            for operation in recipe.operations
            for use in operation.resource_requirements
            if (use.resource_type == "DEVICE" and use.conflict_policy == "STATE_COMPATIBLE")
        )
        if compatible
        else next(
            use
            for recipe in knowledge.recipes
            for operation in recipe.operations
            for use in operation.resource_requirements
            if use.resource_id == "stove_choice"
        )
    )
    if not choice and not compatible:
        use = use.model_copy(update={"resource_id": "burner_1", "component_id": "burner_1"})
    operations = [
        {
            "operation_id": name,
            "action": "WAIT" if name == "wait" else "MIX",
            "duration": {"execution_sec": duration},
            "resource_requirements": [
                use.model_dump(),
                *(
                    []
                    if name == "wait"
                    else [
                        {
                            "resource_type": "HUMAN",
                            "resource_id": "human_1",
                            "conflict_policy": "UNARY",
                        }
                    ]
                ),
            ],
        }
        for name, duration in (("load", 60), ("wait", 120), ("unload", 30))
    ]
    recipe = CanonicalRecipeModel(
        schema_version="1.0",
        recipe_id="synthetic-continuous",
        recipe_version="1",
        name="合成连续设备三阶段",
        operations=operations,
        ingredient_requirements=(),
        material_specs=(),
        provenance_refs=("synthetic:continuous-device",),
        dependencies=[
            {
                "predecessor_id": before,
                "successor_id": after,
                "reason": "合成连续工艺",
                "evidence_refs": ["synthetic:continuous-device"],
            }
            for before, after in (("load", "wait"), ("wait", "unload"))
        ],
    )
    other = recipe.model_copy(
        update={
            "recipe_id": type(recipe.recipe_id)("synthetic-foreign"),
            "name": "合成外来工序",
            "operations": (recipe.operations[0],),
            "dependencies": (),
        }
    )
    context = RecipeSchedulingContext(
        recipe_id=recipe.recipe_id,
        resource_reservations=(
            RecipeReservation(
                reservation_id="synthetic-cavity",
                members=("load", "wait", "unload"),
                resource_options=("burner_1", "burner_2") if choice else (use.resource_id,),
                policy=use.conflict_policy,
                span="min_start_to_max_end",
                origin="SOURCE_EXPLICIT",
            ),
        ),
    )
    knowledge = knowledge.model_copy(
        update={"recipes": (recipe, other), "recipe_contexts": (context,), "rules": ()}
    )
    runtime, planning, current = service(tmp_path, knowledge=knowledge)
    outcome = planning.apply_event(
        event(
            current,
            "start",
            "START_SESSION",
            {"recipes": [{"id": recipe.recipe_id, "name": recipe.name}]},
        )
    )
    assert outcome.status == "PUBLISHED", outcome.model_dump_json()
    return runtime, planning


def feedback(runtime, identity, kind, operation, execution, at, **extra):
    current = runtime.get("flow")
    task = stable_id("task", current.menu[0].recipe_instance_id.root, operation)
    return runtime.apply_event(
        event(current, identity, kind, {"task_id": task, "execution_id": execution, **extra}, at=at)
    )


def between_steps(runtime):
    assert (
        feedback(runtime, "load-start", "OPERATION_STARTED", "load", "load-run", 0).status
        == "APPLIED"
    )
    assert (
        feedback(
            runtime,
            "load-end",
            "OPERATION_COMPLETED",
            "load",
            "load-run",
            60,
            resource_release_status="UNCONFIRMED",
        ).status
        == "APPLIED"
    )
    return runtime.get("flow")


def test_phase_completion_holds_device_then_hands_it_to_next_execution(tmp_path):
    runtime, _ = continuous_service(tmp_path)
    current = between_steps(runtime)
    active = [o for o in current.runtime.details.occupancies if o.released_at is None]
    assert len(active) == 1 and active[0].resource.resource_type == "DEVICE"
    assert not active[0].awaiting_confirmation
    assert (
        feedback(runtime, "wait-start", "OPERATION_STARTED", "wait", "wait-run", 60).status
        == "APPLIED"
    )
    state = runtime.get("flow")
    assert [
        o.execution_id.root for o in state.runtime.details.occupancies if o.released_at is None
    ] == ["wait-run"]
    assert state.runtime.executions[0].finished_at == state.runtime.time_origin.at(60)
    assert state.runtime.executions[0].resource_spans


def test_replan_between_phases_preserves_outer_occupation_and_completes(tmp_path):
    runtime, planning = continuous_service(tmp_path)
    before = between_steps(runtime)
    added = planning.apply_event(
        event(
            before,
            "add",
            "ADD_RECIPE",
            {"recipes": [{"id": "synthetic-foreign", "name": "合成外来工序"}]},
            at=60,
        )
    )
    assert added.status == "PUBLISHED", added.model_dump_json()
    current = runtime.get("flow")
    foreign = stable_id("task", current.menu[1].recipe_instance_id.root, "load")
    assignment = next(
        a
        for a in added.plan.validated.candidate.assignments
        if foreign in {t.root for t in a.task_ids}
    )
    assert assignment.interval.start_sec >= 210
    assert current.runtime.executions == before.runtime.executions
    simulator = Simulator(runtime, "flow")
    simulator.advance(added.plan.validated.candidate.metrics.makespan_sec)
    final = runtime.get("flow")
    assert all(e.status == "COMPLETED" for e in final.runtime.executions)
    assert not any(o.released_at is None for o in final.runtime.details.occupancies)


def test_simulator_does_not_report_clearance_between_continuous_steps(tmp_path):
    runtime, _ = continuous_service(tmp_path)
    events = Simulator(runtime, "flow").advance(60)
    completion = next(e for e in events if e.event_type == "OPERATION_COMPLETED")
    assert completion.payload.resource_release_status == "UNCONFIRMED"
    current = runtime.get("flow")
    assert (
        sum(
            o.released_at is None and o.resource.resource_type == "DEVICE"
            for o in current.runtime.details.occupancies
        )
        == 1
    )


@pytest.mark.parametrize("clear_via_completion", [False, True])
def test_premature_clearance_is_recorded_but_remaining_process_is_blocked(
    tmp_path, clear_via_completion
):
    runtime, planning = continuous_service(tmp_path)
    if clear_via_completion:
        assert (
            feedback(runtime, "load-start", "OPERATION_STARTED", "load", "load-run", 0).status
            == "APPLIED"
        )
        result = feedback(
            runtime,
            "early-clear",
            "OPERATION_COMPLETED",
            "load",
            "load-run",
            60,
            resource_release_status="CONFIRMED",
        )
    else:
        current = between_steps(runtime)
        result = runtime.apply_event(
            event(
                current,
                "early-clear",
                "DEVICE_RELEASE_CONFIRMED",
                {"device_id": "burner_1", "execution_id": "load-run"},
                at=60,
            )
        )
    assert result.status == "APPLIED"
    assert result.requires_replan
    current = runtime.get("flow")
    assert current.runtime.executions[0].status == "COMPLETED"
    assert not any(o.released_at is None for o in current.runtime.details.occupancies)
    assert current.runtime.details.blocked_task_ids
    assert planning.drain("flow").status == "FAILED"


@pytest.mark.parametrize("solver", ["greedy", "cp_sat"])
@pytest.mark.parametrize("choice", [False, True])
def test_both_algorithms_keep_continuous_physical_reservation(tmp_path, solver, choice):
    runtime, _ = continuous_service(tmp_path, choice=choice)
    between_steps(runtime)
    current = runtime.get("flow")
    assert (
        runtime.apply_event(
            event(
                current,
                "add",
                "ADD_RECIPE",
                {"recipes": [{"id": "synthetic-foreign", "name": "合成外来工序"}]},
                at=60,
            )
        ).status
        == "APPLIED"
    )
    current = runtime.get("flow")
    request = prepare_replan(current, None, runtime.knowledge, current.policy)
    deadline = Deadline(expires_at_ns=time.monotonic_ns() + 2_400_000_000)
    problem = ProblemCompiler().compile(
        runtime.knowledge, request.menu, request.runtime, request.policy, deadline
    )
    assert isinstance(problem, SchedulingProblem), problem
    solved = (
        GreedyScheduler().solve(problem, deadline)
        if solver == "greedy"
        else CpSatScheduler().solve(problem, None, deadline)
    )
    candidate = solved.candidate
    assert candidate is not None, solved
    actual = next(o.resource for o in current.runtime.details.occupancies if o.released_at is None)
    members = current.bindings[0].continuities[0].members
    assert all(
        (use.physical_resource_id, use.component_id)
        == (actual.physical_resource_id, actual.component_id)
        for assignment in candidate.assignments
        if set(assignment.task_ids) & set(members)
        for use in assignment.resource_uses
        if use.resource_type == "DEVICE"
    )
    report = ScheduleValidator().validate(runtime.knowledge, request.runtime, problem, candidate)
    assert report.valid, report
    details = request.runtime.details
    held = next(o for o in details.occupancies if o.released_at is None)
    forged = held.model_copy(update={"reservation_id": "unrelated-continuity"})
    tampered = request.runtime.model_copy(
        update={
            "details": details.model_copy(
                update={
                    "occupancies": tuple(forged if o == held else o for o in details.occupancies)
                }
            )
        }
    )
    rejected = ScheduleValidator().validate(runtime.knowledge, tampered, problem, candidate)
    assert not rejected.valid
    assert "STATE_RESOURCE" in {v.code for v in rejected.violations}


def test_handoff_preserves_gap_history_and_rejects_old_owner_release(tmp_path):
    runtime, planning = continuous_service(tmp_path)
    between_steps(runtime)
    assert (
        feedback(runtime, "wait-late", "OPERATION_STARTED", "wait", "wait-run", 65).status
        == "APPLIED"
    )
    before = runtime.get("flow")
    owner = before.runtime.executions[0]
    device_spans = [
        s.interval for s in owner.resource_spans if s.resource.resource_type == "DEVICE"
    ]
    assert [(s.start_sec, s.end_sec) for s in device_spans] == [(0, 60), (60, 65)]
    assert owner.finished_at == before.runtime.time_origin.at(60)
    rejected = runtime.apply_event(
        event(
            before,
            "old-clear",
            "DEVICE_RELEASE_CONFIRMED",
            {"device_id": "burner_1", "execution_id": "load-run"},
            at=70,
        )
    )
    assert rejected.status == "REJECTED"
    assert runtime.get("flow").runtime.details.occupancies == before.runtime.details.occupancies
    replanned = planning.drain("flow")
    assert replanned.status == "PUBLISHED", replanned.model_dump_json()
    Simulator(runtime, "flow").advance(replanned.plan.validated.candidate.metrics.makespan_sec)
    assert all(e.status == "COMPLETED" for e in runtime.get("flow").runtime.executions)


def test_cancellation_keeps_held_resource_until_explicit_clearance(tmp_path):
    runtime, planning = continuous_service(tmp_path)
    current = between_steps(runtime)
    assert (
        runtime.apply_event(
            event(
                current,
                "cancel",
                "CANCEL_RECIPE",
                {"recipe_instance_id": current.menu[0].recipe_instance_id},
                at=60,
            )
        ).status
        == "APPLIED"
    )
    current = runtime.get("flow")
    assert (
        runtime.apply_event(
            event(
                current,
                "add",
                "ADD_RECIPE",
                {"recipes": [{"id": "synthetic-foreign", "name": "合成外来工序"}]},
                at=60,
            )
        ).status
        == "APPLIED"
    )
    assert planning.drain("flow").status == "FAILED"
    held = runtime.get("flow")
    assert any(o.released_at is None for o in held.runtime.details.occupancies)
    cleared = runtime.apply_event(
        event(
            held,
            "clear-cancelled",
            "DEVICE_RELEASE_CONFIRMED",
            {"device_id": "burner_1", "execution_id": "load-run"},
            at=65,
        )
    )
    assert cleared.status == "APPLIED"
    outcome = planning.drain("flow")
    assert outcome.status == "PUBLISHED", outcome.model_dump_json()
    assert runtime.get("flow").runtime.executions[0].finished_at == current.runtime.time_origin.at(
        60
    )


def test_foreign_start_cannot_take_held_cavity_between_steps(tmp_path):
    runtime, planning = continuous_service(tmp_path)
    current = between_steps(runtime)
    outcome = planning.apply_event(
        event(
            current,
            "add",
            "ADD_RECIPE",
            {"recipes": [{"id": "synthetic-foreign", "name": "合成外来工序"}]},
            at=60,
        )
    )
    assert outcome.status == "PUBLISHED", outcome
    current = runtime.get("flow")
    foreign = stable_id("task", current.menu[1].recipe_instance_id.root, "load")
    rejected = runtime.apply_event(
        event(
            current,
            "foreign-too-soon",
            "OPERATION_STARTED",
            {"task_id": foreign, "execution_id": "foreign"},
            at=60,
        )
    )
    assert rejected.status == "CONFLICT"
    after = runtime.get("flow")
    assert after.runtime.details.occupancies == current.runtime.details.occupancies
    assert after.runtime.executions == current.runtime.executions


def test_compatible_storage_keeps_multiple_owners_and_hands_off_only_own_scope(tmp_path):
    runtime, planning = continuous_service(tmp_path, compatible=True)
    before = between_steps(runtime)
    added = planning.apply_event(
        event(
            before,
            "add",
            "ADD_RECIPE",
            {"recipes": [{"id": "synthetic-foreign", "name": "合成外来工序"}]},
            at=60,
        )
    )
    assert added.status == "PUBLISHED", added.model_dump_json()
    current = runtime.get("flow")
    foreign = stable_id("task", current.menu[1].recipe_instance_id.root, "load")
    assignment = next(
        a
        for a in added.plan.validated.candidate.assignments
        if foreign in {t.root for t in a.task_ids}
    )
    # D2 可把主动操作放在等待的后半段；整个外来操作须落在同温区等待内。
    assert 60 <= assignment.interval.start_sec < assignment.interval.end_sec <= 180
    assert (
        feedback(runtime, "wait-start", "OPERATION_STARTED", "wait", "wait-run", 60).status
        == "APPLIED"
    )
    current = runtime.get("flow")
    assert (
        runtime.apply_event(
            event(
                current,
                "foreign-start",
                "OPERATION_STARTED",
                {"task_id": foreign, "execution_id": "foreign-run"},
                at=assignment.interval.start_sec,
            )
        ).status
        == "APPLIED"
    )
    current = runtime.get("flow")
    active = [
        o
        for o in current.runtime.details.occupancies
        if o.released_at is None and o.resource.resource_type == "DEVICE"
    ]
    assert {o.execution_id.root for o in active} == {"foreign-run", "wait-run"}
    Simulator(runtime, "flow").advance(added.plan.validated.candidate.metrics.makespan_sec)
    assert not any(o.released_at is None for o in runtime.get("flow").runtime.details.occupancies)


def test_restart_restores_continuity_binding_and_actual_owner(tmp_path):
    runtime, _ = continuous_service(tmp_path)
    before = between_steps(runtime)
    path, knowledge, clock = runtime.store.path, runtime.knowledge, runtime.clock
    runtime.store.close()
    restored = restore_session(UnitOfWork(path), "flow", clock, lambda ref: knowledge)
    assert restored.get("flow") == before
    assert (
        feedback(restored, "wait-start", "OPERATION_STARTED", "wait", "wait-run", 60).status
        == "APPLIED"
    )
    Simulator(restored, "flow").advance(210)
    assert all(e.status == "COMPLETED" for e in restored.get("flow").runtime.executions)
    assert not any(o.released_at is None for o in restored.get("flow").runtime.details.occupancies)


def test_between_phase_fault_requires_recovery_evidence_even_after_device_recovers(tmp_path):
    runtime, planning = continuous_service(tmp_path)
    current = between_steps(runtime)
    down = runtime.apply_event(
        event(
            current,
            "down",
            "DEVICE_UNAVAILABLE",
            {"device_id": "burner_1", "reason": "合成故障"},
            at=61,
        )
    )
    assert down.status == "APPLIED" and down.requires_replan
    current = runtime.get("flow")
    assert current.runtime.details.blocked_task_ids
    assert current.runtime.executions[0].status == "COMPLETED"
    assert (
        runtime.apply_event(
            event(current, "up", "DEVICE_RECOVERED", {"device_id": "burner_1"}, at=62)
        ).status
        == "APPLIED"
    )
    assert planning.drain("flow").status == "FAILED"
    assert any(o.released_at is None for o in runtime.get("flow").runtime.details.occupancies)
