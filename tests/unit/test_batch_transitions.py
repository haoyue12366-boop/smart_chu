"""显式合成转换表测试；不作为实际设备转换时长依据。"""

import pytest

from app.compiler.transitions import resolve_transition
from app.domain.processing_rules import ProcessingRule
from app.domain.resources import ConfigurationValue
from app.domain.transitions import (
    ThermalProfile,
    ThermalState,
    TransitionHumanPhase,
    TransitionRuleSpec,
    TransitionTarget,
)


def profile(temperature=100, mode="普通蒸"):
    return ThermalProfile(
        physical_resource_id="synthetic-device",
        component_id="chamber",
        profile_id="synthetic-profile",
        configuration=(
            ConfigurationValue(parameter="mode", value=mode),
            ConfigurationValue(parameter="temperature_c", value=temperature),
        ),
    )


def rule(condition="OFF", seconds=300, before=None, after=None, manual=()):
    spec = TransitionRuleSpec(
        from_condition=condition,
        from_profile=before,
        to_profile=after or profile(),
        max_idle_sec=60,
        human_phases=manual,
        authority="DELEGATED_DEVELOPMENT_ESTIMATE",
        authorization_ref="synthetic-delegation",
    )
    return ProcessingRule(
        rule_id="synthetic-" + condition,
        rule_version="transition-test-v1",
        kind="TRANSITION",
        group_compatibility_predicate=spec.model_dump_json(),
        transition_duration_sec=seconds,
        review_status="NEEDS_REVIEW",
        evidence_refs=("synthetic-delegation", "synthetic-transition-table"),
    )


def state(condition="OFF", thermal_profile=None, **kwargs):
    return ThermalState(
        physical_resource_id="synthetic-device",
        component_id="chamber",
        condition=condition,
        profile=thermal_profile,
        at_sec=0,
        origin="OBSERVED",
        source_ref="synthetic-event",
        **kwargs,
    )


def target(rules, at=0, thermal_profile=None, **kwargs):
    return TransitionTarget(
        profile=thermal_profile or profile(),
        at_sec=at,
        rules=tuple(rules),
        release_kind="development",
        allow_delegated_estimates=True,
        **kwargs,
    )


def test_same_temperature_but_switched_off_requires_full_explicit_preheat():
    rules = (rule(), rule("HOLDING", 0, before=profile()))
    plan = resolve_transition(state("OFF", profile()), target(rules), "transition-test-v1")
    assert plan.allowed and plan.duration_sec == 300 and plan.kind == "COLD_START"
    warm = resolve_transition(state("HOLDING", profile()), target(rules), "transition-test-v1")
    assert warm.allowed and warm.duration_sec == 0 and warm.kind == "REUSE"


@pytest.mark.parametrize("condition", ["UNKNOWN", "HOLDING", "OFF"])
def test_missing_transition_table_never_invents_temperature_formula(condition):
    result = resolve_transition(state(condition, profile()), target(()), "transition-test-v1")
    assert not result.allowed and result.duration_sec is None


def test_unknown_temperature_requires_its_own_explicit_reset_rule():
    previous = state("UNKNOWN", profile())
    assert not resolve_transition(previous, target((rule(),)), "transition-test-v1").allowed
    explicit = rule("UNKNOWN", 480)
    result = resolve_transition(previous, target((explicit,)), "transition-test-v1")
    assert result.allowed and result.duration_sec == 480


def test_holding_observation_is_not_unlimited_or_valid_before_it_happened():
    r = rule("HOLDING", 0, before=profile())
    assert resolve_transition(
        state("HOLDING", profile()), target((r,), 60), "transition-test-v1"
    ).allowed
    assert not resolve_transition(
        state("HOLDING", profile()), target((r,), 61), "transition-test-v1"
    ).allowed
    future = state("HOLDING", profile()).model_copy(update={"at_sec": 10})
    assert not resolve_transition(future, target((r,)), "transition-test-v1").allowed


def test_configuration_change_preserves_manual_phase_and_exact_table_duration():
    after = profile(180, "全域烤")
    phases = (TransitionHumanPhase(start_offset_sec=0, end_offset_sec=30),)
    r = rule("HOLDING", 240, before=profile(), after=after, manual=phases)
    result = resolve_transition(
        state("HOLDING", profile()), target((r,), thermal_profile=after), "transition-test-v1"
    )
    assert result.allowed and result.duration_sec == 240
    assert result.kind == "CONFIGURATION_CHANGE" and result.human_phases == phases
    assert not resolve_transition(
        state("HOLDING", profile(90)), target((r,), thermal_profile=after), "transition-test-v1"
    ).allowed


def test_wrong_version_or_development_permission_cannot_apply_rule():
    r = rule()
    assert not resolve_transition(state(), target((r,)), "different-version").allowed
    request = target((r,)).model_copy(update={"allow_delegated_estimates": False})
    assert not resolve_transition(state(), request, "transition-test-v1").allowed
    formal = target((r,)).model_copy(update={"release_kind": "competition"})
    assert not resolve_transition(state(), formal, "transition-test-v1").allowed


def test_actual_completed_transition_is_not_repeated_or_inferred_from_plan():
    r = rule()
    observed = state(
        "TRANSITION_COMPLETED",
        profile(),
        completed_rule_id=r.rule_id,
        completed_rule_version=r.rule_version,
    )
    result = resolve_transition(observed, target((r,)), r.rule_version)
    assert result.allowed and result.duration_sec == 0 and result.kind == "ALREADY_COMPLETED"
    planned = observed.model_copy(update={"origin": "PLANNED"})
    assert not resolve_transition(planned, target((r,)), r.rule_version).allowed
    wrong_target = target((r,), thermal_profile=profile(180, "全域烤"))
    assert not resolve_transition(observed, wrong_target, r.rule_version).allowed


def test_ambiguous_or_invalid_manual_table_is_rejected():
    r = rule()
    other = r.model_copy(update={"rule_id": "another", "transition_duration_sec": 200})
    assert not resolve_transition(state(), target((r, other)), r.rule_version).allowed
    invalid = rule(
        seconds=20, manual=(TransitionHumanPhase(start_offset_sec=0, end_offset_sec=30),)
    )
    assert not resolve_transition(state(), target((invalid,)), invalid.rule_version).allowed


def test_off_state_without_configuration_cannot_be_reused_from_another_device():
    wrong = state().model_copy(update={"physical_resource_id": "other-device"})
    assert not resolve_transition(wrong, target((rule(),)), "transition-test-v1").allowed


# The following fixtures add explicitly synthetic transition and loading-boundary
# evidence to source-derived recipes. They never enter a published release.
def joint_transition_context(condition="HOLDING", duration=0, max_idle=20000, manual=()):
    from app.compiler.thermal_batches import generate_thermal_batches
    from app.domain.base import content_hash
    from app.domain.runtime_snapshot import DeviceState
    from tests.unit.test_joint_thermal_batches import thermal_context

    ctx = thermal_context()
    base = generate_thermal_batches(ctx)[0]
    use = base.resource_uses[0]
    thermal_profile = ThermalProfile(
        physical_resource_id=use.physical_resource_id,
        component_id=use.component_id,
        profile_id=next(
            p.profile_id
            for p in ctx.group.knowledge.profiles
            if p.mode == next(c.value for c in use.configuration if c.parameter == "mode")
            and p.profile_id
            in next(
                d.capability_refs
                for d in ctx.group.knowledge.devices
                if d.device_instance_id == use.resource_id
            )
        ),
        configuration=use.configuration,
    )
    group = next(r for r in ctx.group.knowledge.rules if r.kind == "STRICT_TOGETHER")
    spec = TransitionRuleSpec(
        from_condition="HOLDING",
        from_profile=thermal_profile,
        to_profile=thermal_profile,
        max_idle_sec=max_idle,
        human_phases=manual,
        authority="DELEGATED_DEVELOPMENT_ESTIMATE",
        authorization_ref="synthetic-delegation",
        target_group_rule_id=group.rule_id,
        target_group_rule_hash=content_hash(group),
        load_boundary="ISOLATED_UNTIL_HEAT",
        scope_note="SYNTHETIC isolated loading and transition timing; no device measurements",
    )
    transition = ProcessingRule(
        rule_id="synthetic-joint-transition",
        rule_version=group.rule_version,
        kind="TRANSITION",
        group_compatibility_predicate=spec.model_dump_json(),
        transition_duration_sec=duration,
        review_status="NEEDS_REVIEW",
        evidence_refs=("synthetic-delegation", "synthetic-isolated-loading"),
    )
    observation = ThermalState(
        physical_resource_id=use.physical_resource_id,
        component_id=use.component_id,
        condition=condition,
        profile=thermal_profile,
        at_sec=0,
        origin="OBSERVED",
        source_ref="synthetic-observation",
    )
    device = DeviceState(
        device_instance_id=use.resource_id,
        physical_resource_id=use.physical_resource_id,
        component_id=use.component_id,
        availability_status="AVAILABLE",
        occupancy_status="FREE",
        observed_at=ctx.group.runtime.time_origin.start_at,
        source="SIMULATED",
        thermal_state=observation,
    )
    runtime = ctx.group.runtime.model_copy(update={"device_states": (device,)})
    knowledge = ctx.group.knowledge.model_copy(
        update={"rules": (*ctx.group.knowledge.rules, transition)}
    )
    return ctx.model_copy(
        update={"group": ctx.group.model_copy(update={"knowledge": knowledge, "runtime": runtime})}
    )


def compile_transition(ctx, menu=None):
    from app.compiler.compiler import ProblemCompiler
    from app.domain.policy import SchedulingPolicy
    from app.domain.scheduling_problem import SchedulingProblem
    from tests.unit.test_shared_prep_coverage import deadline

    problem = ProblemCompiler().compile(
        ctx.group.knowledge,
        menu or ctx.group.menu,
        ctx.group.runtime,
        SchedulingPolicy(
            policy_version="synthetic-transition-integration",
            strict_together_batch=True,
            allow_delegated_shared_estimates=True,
        ),
        deadline(),
    )
    assert isinstance(problem, SchedulingProblem), problem
    return problem


def solve_selected_transition(problem, extra_constraints=None):
    from ortools.sat.python import cp_model

    from app.scheduling.model_builder import ModelBuilder
    from tests.unit.test_shared_prep_coverage import deadline

    builder = ModelBuilder(problem, deadline())
    builder.build()
    chosen = next(c for c in problem.thermal_batch_candidates if c.transition_binding is not None)
    builder.model.add(builder.selected[chosen.carrier_id] == 1)
    if extra_constraints:
        extra_constraints(builder, chosen)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 3
    return solver.solve(builder.model), builder, solver


def test_observed_holding_can_select_bound_joint_preheat_reuse():
    from ortools.sat.python import cp_model

    from app.scheduling.solution_mapping import map_solution
    from app.validation.schedule import ScheduleValidator

    ctx = joint_transition_context()
    problem = compile_transition(ctx)
    assert len(problem.thermal_batch_candidates) == 2
    reused = next(c for c in problem.thermal_batch_candidates if c.transition_binding is not None)
    assert reused.duration_sec == 1200
    heats = {t.task_id for t in problem.logical_tasks if t.operation.action == "HEAT"}
    assert {
        (p.start_offset_sec, p.end_offset_sec) for p in reused.member_offsets if p.task_id in heats
    } == {(240, 960)}
    status, builder, solver = solve_selected_transition(problem)
    assert status in {cp_model.OPTIMAL, cp_model.FEASIBLE}
    report = ScheduleValidator().validate(
        ctx.group.knowledge, ctx.group.runtime, problem, map_solution(builder, solver)
    )
    assert report.valid, report.violations


@pytest.mark.parametrize("condition,max_idle", [("OFF", 20000), ("HOLDING", 60)])
def test_joint_reuse_cannot_ignore_power_off_or_expired_observation(condition, max_idle):
    from ortools.sat.python import cp_model

    ctx = joint_transition_context(condition=condition, max_idle=max_idle)
    problem = compile_transition(ctx)
    status, _, _ = solve_selected_transition(problem)
    assert status == cp_model.INFEASIBLE


def test_inserted_third_batch_invalidates_old_initial_state_reuse():
    from ortools.sat.python import cp_model

    from app.domain.ids import RecipeInstanceId

    ctx = joint_transition_context()
    third = ctx.group.menu[0].model_copy(
        update={"recipe_instance_id": RecipeInstanceId("inserted-third")}
    )
    problem = compile_transition(ctx, (*ctx.group.menu, third))
    third_end = next(
        t.task_id
        for t in problem.logical_tasks
        if t.recipe_instance_id == third.recipe_instance_id
        and t.operation.action == "UNLOAD"
        and any(u.resource_id == "steam_oven_1" for u in t.operation.resource_requirements)
    )

    def before(builder, chosen):
        builder.model.add(builder.carrier_starts[chosen.carrier_id] >= builder.ends[third_end])

    status, _, _ = solve_selected_transition(problem, before)
    assert status == cp_model.INFEASIBLE


def test_greedy_accepts_valid_reuse_and_rejects_reuse_after_inserted_successor_change():
    from app.domain.schedule import CandidateSchedule
    from app.scheduling.calendars import CalendarState
    from app.scheduling.layouts import recipe_layout
    from app.scheduling.placement import find_layout_placement
    from app.validation.schedule import ScheduleValidator
    from tests.unit.test_shared_prep_coverage import deadline

    ctx = joint_transition_context()
    problem = compile_transition(ctx)
    reused = next(c for c in problem.thermal_batch_candidates if c.transition_binding)
    proposal = recipe_layout(problem, None, 0, deadline(), (reused,))
    placed = find_layout_placement(proposal, CalendarState(problem), problem, deadline())
    assert placed.assignments
    plan = CandidateSchedule(problem_hash=problem.problem_hash, assignments=placed.assignments)
    assert ScheduleValidator().validate(ctx.group.knowledge, ctx.group.runtime, problem, plan).valid
    off = ctx.group.runtime.device_states[0].model_copy(
        update={
            "thermal_state": ctx.group.runtime.device_states[0].thermal_state.model_copy(
                update={"condition": "OFF"}
            )
        }
    )
    bad_runtime = ctx.group.runtime.model_copy(update={"device_states": (off,)})
    altered = problem.model_copy(update={"runtime": bad_runtime})
    bad = find_layout_placement(proposal, CalendarState(altered), altered, deadline())
    assert not bad.assignments and any("转换" in reason for reason in bad.rejection_reasons)
    false_plan = plan.model_copy(update={"problem_hash": altered.problem_hash})
    report = ScheduleValidator().validate(ctx.group.knowledge, bad_runtime, altered, false_plan)
    assert not report.valid and "THERMAL_TRANSITION" in {v.code for v in report.violations}


def test_transition_manual_work_is_inside_batch_and_counted_once():
    from ortools.sat.python import cp_model

    from app.scheduling.metrics import compute_metrics
    from app.scheduling.solution_mapping import map_solution
    from app.validation.schedule import ScheduleValidator

    ctx = joint_transition_context(
        duration=60, manual=(TransitionHumanPhase(start_offset_sec=0, end_offset_sec=30),)
    )
    problem = compile_transition(ctx)
    reused = next(c for c in problem.thermal_batch_candidates if c.transition_binding)
    assert reused.duration_sec == 1260
    assert len(reused.resource_phases) == 7
    status, builder, solver = solve_selected_transition(problem)
    assert status in {cp_model.OPTIMAL, cp_model.FEASIBLE}
    plan = map_solution(builder, solver)
    metrics = compute_metrics(plan, problem)
    source_work = sum(
        t.operation.duration.execution_sec
        for t in problem.logical_tasks
        if any(u.resource_type == "HUMAN" for u in t.operation.resource_requirements)
    )
    assert metrics.total_human_work_sec == source_work + 30
    report = ScheduleValidator().validate(
        ctx.group.knowledge,
        ctx.group.runtime,
        problem,
        plan.model_copy(update={"metrics": metrics}),
    )
    assert report.valid, report.violations


def test_greedy_insertion_rechecks_existing_successor_without_moving_it():
    from app.domain.ids import RecipeInstanceId
    from app.domain.time import Interval
    from app.scheduling.calendars import CalendarState
    from app.scheduling.layouts import recipe_layout
    from app.scheduling.placement import find_layout_placement
    from tests.unit.test_shared_prep_coverage import deadline

    ctx = joint_transition_context(max_idle=60000)
    pair = compile_transition(ctx)
    selected = next(c for c in pair.thermal_batch_candidates if c.transition_binding)
    pair_layout = recipe_layout(pair, None, 0, deadline(), (selected,))
    delayed = tuple(
        a.model_copy(
            update={
                "interval": Interval(
                    start_sec=a.interval.start_sec + 15000, end_sec=a.interval.end_sec + 15000
                )
            }
        )
        for a in pair_layout
    )
    third = ctx.group.menu[0].model_copy(
        update={"recipe_instance_id": RecipeInstanceId("inserted-third")}
    )
    problem = compile_transition(ctx, (*ctx.group.menu, third))
    calendar = CalendarState(problem)
    existing = find_layout_placement(delayed, calendar, problem, deadline())
    assert existing.assignments, existing.rejection_reasons
    calendar.commit(existing)
    before = calendar.state_hash
    added_layout = recipe_layout(problem, third.recipe_instance_id, 0, deadline())
    insertion = find_layout_placement(added_layout, calendar, problem, deadline())
    assert not insertion.assignments
    assert any("转换" in reason for reason in insertion.rejection_reasons)
    assert calendar.state_hash == before


@pytest.mark.parametrize("with_manual", [False, True])
def test_transition_manual_phase_competes_with_another_recipes_cutting(with_manual):
    from ortools.sat.python import cp_model

    from app.domain.ids import RecipeInstanceId

    ctx = joint_transition_context(
        duration=60,
        manual=(TransitionHumanPhase(start_offset_sec=0, end_offset_sec=30),)
        if with_manual
        else (),
    )
    third = ctx.group.menu[0].model_copy(
        update={"recipe_instance_id": RecipeInstanceId("third-manual")}
    )
    problem = compile_transition(ctx, (*ctx.group.menu, third))
    cut = next(
        t.task_id
        for t in problem.logical_tasks
        if t.recipe_instance_id == third.recipe_instance_id and t.operation.action == "CUT"
    )

    def collision(builder, chosen):
        builder.model.add(
            builder.starts[cut]
            == builder.carrier_starts[chosen.carrier_id]
            + chosen.transition_binding.transition_offset_sec
        )

    status, _, _ = solve_selected_transition(problem, collision)
    if with_manual:
        assert status == cp_model.INFEASIBLE
    else:
        assert status in {cp_model.FEASIBLE, cp_model.OPTIMAL}


def completed_transition_context():
    ctx = joint_transition_context(
        duration=60, manual=(TransitionHumanPhase(start_offset_sec=0, end_offset_sec=30),)
    )
    rule = next(r for r in ctx.group.knowledge.rules if r.kind == "TRANSITION")
    device = ctx.group.runtime.device_states[0]
    observed = device.thermal_state.model_copy(
        update={
            "condition": "TRANSITION_COMPLETED",
            "completed_rule_id": rule.rule_id,
            "completed_rule_version": rule.rule_version,
        }
    )
    runtime = ctx.group.runtime.model_copy(
        update={"device_states": (device.model_copy(update={"thermal_state": observed}),)}
    )
    return ctx.model_copy(update={"group": ctx.group.model_copy(update={"runtime": runtime})})


@pytest.mark.parametrize("credit", [False, True])
def test_actual_completed_transition_selects_zero_time_credit_only(credit):
    from ortools.sat.python import cp_model

    from app.scheduling.calendars import CalendarState
    from app.scheduling.layouts import recipe_layout
    from app.scheduling.model_builder import ModelBuilder
    from app.scheduling.placement import find_layout_placement
    from app.scheduling.solution_mapping import map_solution
    from app.validation.schedule import ScheduleValidator
    from tests.unit.test_shared_prep_coverage import deadline

    ctx = completed_transition_context()
    problem = compile_transition(ctx)
    candidates = [c for c in problem.thermal_batch_candidates if c.transition_binding]
    assert len(candidates) == 2
    chosen = next(c for c in candidates if bool(c.transition_binding.completed_state_ref) == credit)
    builder = ModelBuilder(problem, deadline())
    builder.build()
    builder.model.add(builder.selected[chosen.carrier_id] == 1)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 3
    status = solver.solve(builder.model)
    proposal = recipe_layout(problem, None, 0, deadline(), (chosen,))
    placed = find_layout_placement(proposal, CalendarState(problem), problem, deadline())
    if credit:
        assert status in {cp_model.OPTIMAL, cp_model.FEASIBLE}
        assert chosen.duration_sec == 1200 and len(chosen.resource_phases) == 6
        assert placed.assignments
        plan = map_solution(builder, solver)
        assert (
            ScheduleValidator()
            .validate(ctx.group.knowledge, ctx.group.runtime, problem, plan)
            .valid
        )
        device = ctx.group.runtime.device_states[0]
        altered = device.thermal_state.model_copy(update={"source_ref": "different-event"})
        runtime = ctx.group.runtime.model_copy(
            update={"device_states": (device.model_copy(update={"thermal_state": altered}),)}
        )
        forged = problem.model_copy(update={"runtime": runtime})
        report = ScheduleValidator().validate(
            ctx.group.knowledge,
            runtime,
            forged,
            plan.model_copy(update={"problem_hash": forged.problem_hash}),
        )
        assert not report.valid and "THERMAL_TRANSITION" in {v.code for v in report.violations}
    else:
        assert status == cp_model.INFEASIBLE
        assert not placed.assignments


def test_later_observation_supersedes_completed_device_history():
    from datetime import timedelta

    from ortools.sat.python import cp_model

    from app.domain.ids import RecipeInstanceId
    from app.domain.runtime_snapshot import ExecutionRecord
    from app.scheduling.greedy import GreedyScheduler
    from app.scheduling.solution_mapping import map_solution
    from app.validation.schedule import ScheduleValidator
    from tests.unit.test_shared_prep_coverage import deadline

    ctx = joint_transition_context(max_idle=60000)
    third = ctx.group.menu[0].model_copy(
        update={"recipe_instance_id": RecipeInstanceId("past-third")}
    )
    historical = compile_transition(ctx, (third,))
    plan = GreedyScheduler().solve(historical, deadline()).candidate
    assert plan is not None
    now = max(a.interval.end_sec for a in plan.assignments) + 1
    origin = ctx.group.runtime.time_origin.start_at
    executions = tuple(
        ExecutionRecord(
            execution_id=f"synthetic-history-{i}",
            task_ids=a.task_ids,
            status="COMPLETED",
            source="SIMULATED",
            event_refs=(f"synthetic-completion-{i}",),
            started_at=origin + timedelta(seconds=a.interval.start_sec),
            finished_at=origin + timedelta(seconds=a.interval.end_sec),
            resource_ids=tuple(u.resource_id for u in a.resource_uses),
        )
        for i, a in enumerate(plan.assignments)
    )
    device = ctx.group.runtime.device_states[0]
    observed = device.thermal_state.model_copy(update={"at_sec": now})
    runtime = ctx.group.runtime.model_copy(
        update={
            "now_offset_sec": now,
            "executions": executions,
            "device_states": (
                device.model_copy(
                    update={
                        "observed_at": origin + timedelta(seconds=now),
                        "thermal_state": observed,
                    }
                ),
            ),
        }
    )
    ctx = ctx.model_copy(update={"group": ctx.group.model_copy(update={"runtime": runtime})})
    problem = compile_transition(ctx, (*ctx.group.menu, third))
    status, builder, solver = solve_selected_transition(problem)
    assert status in {cp_model.OPTIMAL, cp_model.FEASIBLE}
    report = ScheduleValidator().validate(
        ctx.group.knowledge, runtime, problem, map_solution(builder, solver)
    )
    assert report.valid, report.violations
