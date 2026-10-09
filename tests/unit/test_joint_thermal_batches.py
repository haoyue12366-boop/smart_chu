"""H02源工序完整映射；热边界为用户委托的开发模拟，未声称实机测量。"""

import pytest
from ortools.sat.python import cp_model

from app.compiler.thermal_batches import generate_thermal_batches
from app.domain.compatibility import GroupRuleSpec
from tests.unit.test_shared_prep_coverage import context, deadline


def thermal_context():
    ctx = context()
    rules = []
    for rule in ctx.group.knowledge.rules:
        if rule.kind == "STRICT_TOGETHER":
            spec = GroupRuleSpec.model_validate_json(rule.group_compatibility_predicate)
            spec = spec.model_copy(
                update={"thermal_model": "COLD_LOAD_SETUP_PREHEAT_HEAT_STOP_UNLOAD"}
            )
            rule = rule.model_copy(update={"group_compatibility_predicate": spec.model_dump_json()})
        rules.append(rule)
    return ctx.model_copy(
        update={
            "group": ctx.group.model_copy(
                update={"knowledge": ctx.group.knowledge.model_copy(update={"rules": tuple(rules)})}
            )
        }
    )


def test_joint_batch_preserves_ten_source_operations_and_full_1500_seconds():
    ctx = thermal_context()
    batches = generate_thermal_batches(ctx)
    assert len(batches) == 1
    batch = batches[0]
    assert batch.duration_sec == 1500
    assert len(batch.covers) == 10
    assert len(batch.member_offsets) == 10
    assert len(batch.resource_uses) == 1
    assert batch.resource_uses[0].resource_type == "DEVICE"
    assert len(batch.resource_phases) == 6
    assert sum(p.end_offset_sec - p.start_offset_sec for p in batch.resource_phases) == 480
    by_task = {t.task_id: t for t in ctx.instantiated.tasks}
    heat = [p for p in batch.member_offsets if by_task[p.task_id].operation.action == "HEAT"]
    assert {(p.start_offset_sec, p.end_offset_sec) for p in heat} == {(540, 1260)}
    setup = [p for p in batch.member_offsets if by_task[p.task_id].operation.action == "PREPARE"]
    assert {(p.start_offset_sec, p.end_offset_sec) for p in setup} == {(120, 240)}
    assert len(batch.replaced_reservation_ids) == 2


def test_missing_explicit_thermal_boundary_cannot_create_batch():
    assert generate_thermal_batches(context()) == ()


@pytest.fixture(scope="module")
def solved_batch():
    from app.compiler.compiler import ProblemCompiler
    from app.domain.policy import SchedulingPolicy
    from app.domain.scheduling_problem import SchedulingProblem
    from app.scheduling.model_builder import ModelBuilder
    from app.scheduling.solution_mapping import map_solution
    from app.validation.schedule import ScheduleValidator

    ctx = thermal_context()
    problem = ProblemCompiler().compile(
        ctx.group.knowledge,
        ctx.group.menu,
        ctx.group.runtime,
        SchedulingPolicy(
            policy_version="p3-thermal-test",
            strict_together_batch=True,
            allow_delegated_shared_estimates=True,
        ),
        deadline(),
    )
    assert isinstance(problem, SchedulingProblem), problem
    assert len(problem.thermal_batch_candidates) == 1
    batch = problem.thermal_batch_candidates[0]
    builder = ModelBuilder(problem, deadline())
    builder.build()
    builder.model.add(builder.selected[batch.carrier_id] == 1)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 3
    assert solver.solve(builder.model) in {cp_model.OPTIMAL, cp_model.FEASIBLE}, (
        builder.model.validate()
    )
    plan = map_solution(builder, solver)
    report = ScheduleValidator().validate(ctx.group.knowledge, ctx.group.runtime, problem, plan)
    assert report.valid, report.violations
    return ctx, problem, plan


def test_compiler_solver_and_independent_validator_keep_heat_and_human_phases(solved_batch):
    ctx, problem, plan = solved_batch
    batch = problem.thermal_batch_candidates[0]
    joint = next(a for a in plan.assignments if a.carrier_id == batch.carrier_id)
    assert joint.interval.end_sec - joint.interval.start_sec == 1500
    assert not any(set(a.task_ids) & set(batch.covers) for a in plan.assignments if a != joint)


@pytest.mark.parametrize(
    "damage",
    ["heat_short", "manual_missing", "manual_overlap", "reservation_missing", "device_config"],
)
def test_independent_validator_rejects_corrupted_joint_batch(solved_batch, damage):
    from app.validation.schedule import ScheduleValidator

    ctx, problem, plan = solved_batch
    batch = problem.thermal_batch_candidates[0]
    if damage == "heat_short":
        heat_ids = {t.task_id for t in problem.logical_tasks if t.operation.action == "HEAT"}
        update = {
            "member_offsets": tuple(
                p.model_copy(update={"end_offset_sec": p.end_offset_sec - 1})
                if p.task_id in heat_ids
                else p
                for p in batch.member_offsets
            )
        }
    elif damage == "manual_missing":
        update = {"resource_phases": batch.resource_phases[:-1]}
    elif damage == "manual_overlap":
        phases = list(batch.resource_phases)
        phases[1] = phases[1].model_copy(update={"start_offset_sec": 0})
        update = {"resource_phases": tuple(phases)}
    elif damage == "reservation_missing":
        update = {"replaced_reservation_ids": batch.replaced_reservation_ids[:-1]}
    else:
        use = batch.resource_uses[0]
        update = {"resource_uses": (use.model_copy(update={"configuration": ()}),)}
    corrupt = batch.model_copy(update=update)
    problem = problem.model_copy(update={"thermal_batch_candidates": (corrupt,)})
    plan = plan.model_copy(
        update={
            "problem_hash": problem.problem_hash,
            "assignments": tuple(
                a.model_copy(update={"resource_uses": corrupt.resource_uses})
                if a.carrier_id == corrupt.carrier_id
                else a
                for a in plan.assignments
            ),
        }
    )
    report = ScheduleValidator().validate(ctx.group.knowledge, ctx.group.runtime, problem, plan)
    assert not report.valid
    assert "THERMAL_BATCH" in {v.code for v in report.violations}


def test_joint_metrics_and_calendar_count_real_manual_actions_once(solved_batch):
    from app.scheduling.calendar_projection import entries_for
    from app.scheduling.metrics import compute_metrics
    from app.validation.schedule import ScheduleValidator

    ctx, problem, plan = solved_batch
    batch = problem.thermal_batch_candidates[0]
    joint = next(a for a in plan.assignments if a.carrier_id == batch.carrier_id)
    entries = entries_for(problem, (joint,))
    assert len(entries) == 7
    assert (
        sum(
            e.interval.end_sec - e.interval.start_sec
            for e in entries
            if e.use.resource_type == "HUMAN"
        )
        == 480
    )
    assert (
        sum(
            e.interval.end_sec - e.interval.start_sec
            for e in entries
            if e.use.resource_type == "DEVICE"
        )
        == 1500
    )
    metrics = compute_metrics(plan, problem)
    expected = sum(
        t.operation.duration.execution_sec
        for t in problem.logical_tasks
        if any(u.resource_type == "HUMAN" for u in t.operation.resource_requirements)
    )
    assert metrics.total_human_work_sec == expected
    report = ScheduleValidator().validate(
        ctx.group.knowledge,
        ctx.group.runtime,
        problem,
        plan.model_copy(update={"metrics": metrics}),
    )
    assert report.valid, report.violations


def test_greedy_builds_and_validates_joint_layout(solved_batch):
    from app.domain.schedule import CandidateSchedule
    from app.scheduling.calendars import CalendarState
    from app.scheduling.layouts import recipe_layout
    from app.scheduling.placement import find_layout_placement
    from app.validation.schedule import ScheduleValidator

    ctx, problem, _ = solved_batch
    proposal = recipe_layout(problem, None, 0, deadline(), problem.thermal_batch_candidates)
    placement = find_layout_placement(proposal, CalendarState(problem), problem, deadline())
    assert placement.assignments, placement.rejection_reasons
    plan = CandidateSchedule(problem_hash=problem.problem_hash, assignments=placement.assignments)
    report = ScheduleValidator().validate(ctx.group.knowledge, ctx.group.runtime, problem, plan)
    assert report.valid, report.violations


@pytest.mark.parametrize("kind", ["thermal", "prep"])
def test_human_objective_allows_selected_group_and_matches_physical_work(solved_batch, kind):
    from app.compiler.compiler import ProblemCompiler
    from app.domain.objectives import ObjectiveStage
    from app.domain.policy import SchedulingPolicy
    from app.scheduling.metrics import compute_metrics
    from app.scheduling.model_builder import ModelBuilder
    from app.scheduling.objectives import apply_stage
    from app.scheduling.solution_mapping import map_solution
    from app.validation.schedule import ScheduleValidator

    ctx, problem, _ = solved_batch
    if kind == "prep":
        problem = ProblemCompiler().compile(
            ctx.group.knowledge,
            ctx.group.menu,
            ctx.group.runtime,
            SchedulingPolicy(
                policy_version="p3-prep-human",
                shared_prep=True,
                allow_delegated_shared_estimates=True,
            ),
            deadline(),
        )
        chosen = problem.shared_prep_candidates[0]
    else:
        chosen = problem.thermal_batch_candidates[0]
    builder = ModelBuilder(problem, deadline())
    builder.build()
    builder.model.add(builder.selected[chosen.carrier_id] == 1)
    # Obtain a real feasible witness, then verify that adding the human objective
    # preserves this exact plan. Search speed under the service budget is separate.
    witness = cp_model.CpSolver()
    witness.parameters.max_time_in_seconds = 3
    assert witness.solve(builder.model) in {cp_model.OPTIMAL, cp_model.FEASIBLE}
    for candidate in builder.candidates:
        present = witness.boolean_value(builder.selected[candidate.carrier_id])
        builder.model.add(builder.selected[candidate.carrier_id] == int(present))
        if present:
            variable = builder.carrier_starts[candidate.carrier_id]
            builder.model.add(variable == witness.value(variable))
    apply_stage(builder, ObjectiveStage(name="D_HUMAN"))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 8
    solver.parameters.num_search_workers = 2
    assert solver.solve(builder.model) in {cp_model.OPTIMAL, cp_model.FEASIBLE}
    plan = map_solution(builder, solver)
    metrics = compute_metrics(plan, problem)
    assert solver.value(builder.objective_variable) == metrics.max_continuous_human_sec
    report = ScheduleValidator().validate(ctx.group.knowledge, ctx.group.runtime, problem, plan)
    assert report.valid, report.violations


def synthetic_thermal_context(device_id="steam_oven_1", second_duration=1200, mismatch=None):
    """显式合成：复用真实菜谱结构，仅在内存改为20分钟蒸/烤边界，不发布。"""
    from app.compiler.candidate_generation import standalone_candidates
    from app.compiler.instantiate import instantiate
    from app.compiler.material_flow import bind_candidate_materials
    from app.domain.base import content_hash
    from app.domain.resources import ConfigurationValue

    ctx = thermal_context()
    k = ctx.group.knowledge
    selected_ids = [
        b.recipe_id
        for r in k.rules
        if r.kind == "STRICT_TOGETHER"
        for b in GroupRuleSpec.model_validate_json(r.group_compatibility_predicate).bindings
    ]
    recipes = []
    profile = next(
        p
        for p in k.profiles
        if p.device_type == ("蒸箱" if device_id == "steam_oven_1" else "烤箱")
    )
    for recipe in k.recipes:
        if recipe.recipe_id not in selected_ids:
            recipes.append(recipe)
            continue
        second = recipe.recipe_id == selected_ids[1]
        operations = []
        for op in recipe.operations:
            uses = []
            for use in op.resource_requirements:
                if use.resource_id == "steam_oven_1":
                    config = (
                        ConfigurationValue(parameter="mode", value=profile.mode),
                        ConfigurationValue(
                            parameter="temperature_c",
                            value=100 if device_id == "steam_oven_1" else 180,
                        ),
                    )
                    if second and mismatch == "mode":
                        config = (
                            ConfigurationValue(parameter="mode", value="合成不兼容模式"),
                            *config[1:],
                        )
                    if mismatch == "humidity":
                        config = (
                            *config,
                            ConfigurationValue(
                                parameter="humidity", value="高" if second else "低"
                            ),
                        )
                    use = use.model_copy(
                        update={
                            "resource_id": device_id,
                            "physical_resource_id": device_id,
                            "profile_options": (profile.profile_id,),
                            "configuration": config,
                        }
                    )
                uses.append(use)
            op = op.model_copy(update={"resource_requirements": tuple(uses)})
            if op.operation_id.root == "op_008_03":
                op = op.model_copy(
                    update={
                        "duration": op.duration.model_copy(
                            update={"execution_sec": second_duration if second else 1200}
                        )
                    }
                )
            operations.append(op)
        recipes.append(recipe.model_copy(update={"operations": tuple(operations)}))
    recipe_map = {r.recipe_id: r for r in recipes}
    rule = next(r for r in k.rules if r.kind == "STRICT_TOGETHER")
    spec = GroupRuleSpec.model_validate_json(rule.group_compatibility_predicate)
    bindings = tuple(
        b.model_copy(
            update={
                "recipe_hash": recipe_map[b.recipe_id].semantic_hash(),
                "operation_hash": content_hash(
                    next(
                        o
                        for o in recipe_map[b.recipe_id].operations
                        if o.operation_id == b.operation_id
                    )
                ),
                "configuration_keys": ("synthetic-common",),
            }
        )
        for b in spec.bindings
    )
    spec = spec.model_copy(
        update={
            "bindings": bindings,
            "duration_sec": 1200,
            "scope_note": "SYNTHETIC thermal boundary test, never published",
        }
    )
    rule = rule.model_copy(update={"group_compatibility_predicate": spec.model_dump_json()})
    contexts = tuple(
        c.model_copy(
            update={
                "resource_reservations": tuple(
                    r.model_copy(update={"resource_options": (device_id,)})
                    if "steam_oven_1" in r.resource_options
                    else r
                    for r in c.resource_reservations
                )
            }
        )
        if c.recipe_id in selected_ids
        else c
        for c in k.recipe_contexts
    )
    k = k.model_copy(
        update={"recipes": tuple(recipes), "rules": (rule,), "recipe_contexts": contexts}
    )
    group = ctx.group.model_copy(update={"knowledge": k})
    inst = instantiate(group.menu, k, group.runtime)
    singles = bind_candidate_materials(standalone_candidates(inst, k), inst)
    return ctx.model_copy(
        update={"group": group, "instantiated": inst, "standalone": singles, "deadline": deadline()}
    )


@pytest.mark.parametrize("device", ["steam_oven_1", "oven_1"])
@pytest.mark.parametrize("seconds", [480, 600, 1200])
def test_synthetic_steam_and_oven_equal_twenty_minutes_only(device, seconds):
    ctx = synthetic_thermal_context(device, seconds)
    batches = generate_thermal_batches(ctx)
    assert bool(batches) == (seconds == 1200)
    if batches:
        batch = batches[0]
        heat_ids = {t.task_id for t in ctx.instantiated.tasks if t.operation.action == "HEAT"}
        ports = [p for p in batch.member_offsets if p.task_id in heat_ids]
        assert len(ports) == 2
        assert {(p.start_offset_sec, p.end_offset_sec) for p in ports} == {(540, 1740)}
        assert batch.duration_sec == 1980
        assert batch.resource_uses[0].resource_id == device


@pytest.mark.parametrize("mismatch", ["mode", "humidity"])
def test_synthetic_same_temperature_does_not_override_mode_or_humidity(mismatch):
    ctx = synthetic_thermal_context(mismatch=mismatch)
    assert generate_thermal_batches(ctx) == ()


def test_thermal_member_hard_windows_must_intersect():
    ctx = thermal_context()
    heat_ids = [t.task_id for t in ctx.instantiated.tasks if t.operation_id.root == "op_008_03"]
    tasks = tuple(
        t.model_copy(update={"latest_end_sec": 1000})
        if t.task_id == heat_ids[0]
        else t.model_copy(update={"earliest_start_sec": 2000})
        if t.task_id == heat_ids[1]
        else t
        for t in ctx.instantiated.tasks
    )
    ctx = ctx.model_copy(
        update={"instantiated": ctx.instantiated.model_copy(update={"tasks": tasks})}
    )
    assert generate_thermal_batches(ctx) == ()


@pytest.mark.parametrize("device", ["steam_oven_1", "oven_1"])
def test_synthetic_twenty_minute_joint_solves_and_validates(device):
    from app.compiler.compiler import ProblemCompiler
    from app.domain.policy import SchedulingPolicy
    from app.domain.scheduling_problem import SchedulingProblem
    from app.scheduling.model_builder import ModelBuilder
    from app.scheduling.solution_mapping import map_solution
    from app.validation.schedule import ScheduleValidator

    ctx = synthetic_thermal_context(device)
    problem = ProblemCompiler().compile(
        ctx.group.knowledge,
        ctx.group.menu,
        ctx.group.runtime,
        SchedulingPolicy(
            policy_version="synthetic-steam-oven",
            strict_together_batch=True,
            allow_delegated_shared_estimates=True,
        ),
        deadline(),
    )
    assert isinstance(problem, SchedulingProblem), problem
    assert len(problem.thermal_batch_candidates) == 1
    builder = ModelBuilder(problem, deadline())
    builder.build()
    builder.model.add(builder.selected[problem.thermal_batch_candidates[0].carrier_id] == 1)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 3
    assert solver.solve(builder.model) in {cp_model.OPTIMAL, cp_model.FEASIBLE}
    report = ScheduleValidator().validate(
        ctx.group.knowledge, ctx.group.runtime, problem, map_solution(builder, solver)
    )
    assert report.valid, report.violations


def test_late_member_can_make_joint_worse_so_standalone_remains_selectable(solved_batch):
    from app.scheduling.greedy import GreedyScheduler
    from app.scheduling.model_builder import ModelBuilder
    from app.scheduling.solution_mapping import map_solution
    from app.validation.schedule import ScheduleValidator

    ctx, problem, _ = solved_batch
    second = ctx.group.menu[1].recipe_instance_id
    problem = problem.model_copy(
        update={
            "logical_tasks": tuple(
                t.model_copy(update={"earliest_start_sec": 10000})
                if t.recipe_instance_id == second and t.operation.action == "LOAD"
                else t
                for t in problem.logical_tasks
            )
        }
    )
    batch = problem.thermal_batch_candidates[0]
    makespans = []
    for joint in (False, True):
        builder = ModelBuilder(problem, deadline())
        builder.build()
        builder.model.add(builder.selected[batch.carrier_id] == int(joint))
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 3
        assert solver.solve(builder.model) == cp_model.OPTIMAL
        plan = map_solution(builder, solver)
        assert (
            ScheduleValidator()
            .validate(ctx.group.knowledge, ctx.group.runtime, problem, plan)
            .valid
        )
        makespans.append(solver.value(builder.makespan))
    assert makespans[0] < makespans[1]
    greedy = GreedyScheduler().solve(problem, deadline())
    assert greedy.candidate is not None
    assert all(a.carrier_id != batch.carrier_id for a in greedy.candidate.assignments)
    assert (
        ScheduleValidator()
        .validate(ctx.group.knowledge, ctx.group.runtime, problem, greedy.candidate)
        .valid
    )


@pytest.mark.parametrize("joint_first", [True, False])
def test_joint_ab_and_independent_c_can_use_either_device_order(solved_batch, joint_first):
    from app.compiler.compiler import ProblemCompiler
    from app.domain.ids import RecipeInstanceId
    from app.domain.scheduling_problem import SchedulingProblem
    from app.scheduling.model_builder import ModelBuilder
    from app.scheduling.solution_mapping import map_solution
    from app.validation.schedule import ScheduleValidator

    ctx, base, _ = solved_batch
    third = ctx.group.menu[0].model_copy(
        update={"recipe_instance_id": RecipeInstanceId("third-serving")}
    )
    problem = ProblemCompiler().compile(
        ctx.group.knowledge, (*ctx.group.menu, third), ctx.group.runtime, base.policy, deadline()
    )
    assert isinstance(problem, SchedulingProblem), problem
    original_tasks = {
        t.task_id for t in problem.logical_tasks if t.recipe_instance_id != third.recipe_instance_id
    }
    batch = next(c for c in problem.thermal_batch_candidates if set(c.covers) <= original_tasks)
    builder = ModelBuilder(problem, deadline())
    builder.build()
    builder.model.add(builder.selected[batch.carrier_id] == 1)
    load = next(
        t.task_id
        for t in problem.logical_tasks
        if t.recipe_instance_id == third.recipe_instance_id
        and t.operation.action == "LOAD"
        and any(u.resource_id == "steam_oven_1" for u in t.operation.resource_requirements)
    )
    unload = next(
        t.task_id
        for t in problem.logical_tasks
        if t.recipe_instance_id == third.recipe_instance_id
        and t.operation.action == "UNLOAD"
        and any(u.resource_id == "steam_oven_1" for u in t.operation.resource_requirements)
    )
    if joint_first:
        builder.model.add(builder.starts[load] >= builder.carrier_ends[batch.carrier_id])
    else:
        builder.model.add(builder.carrier_starts[batch.carrier_id] >= builder.ends[unload])
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 3
    assert solver.solve(builder.model) in {cp_model.OPTIMAL, cp_model.FEASIBLE}
    report = ScheduleValidator().validate(
        ctx.group.knowledge, ctx.group.runtime, problem, map_solution(builder, solver)
    )
    assert report.valid, report.violations
