"""真实规则生成与实际CP-SAT精确覆盖；强制选择只用于测试见证。"""

import time

import pytest
from ortools.sat.python import cp_model

from app.compiler.candidate_generation import standalone_candidates
from app.compiler.compiler import ProblemCompiler
from app.compiler.instantiate import instantiate
from app.compiler.material_flow import bind_candidate_materials
from app.compiler.shared_prep import generate_shared_prep
from app.domain.candidates import SharedCandidateContext
from app.domain.compatibility import GroupContext
from app.domain.ids import CarrierId
from app.domain.policy import SchedulingPolicy
from app.domain.ports import Deadline
from app.domain.scheduling_problem import SchedulingProblem
from app.scheduling.model_builder import ModelBuilder
from tests.shared_support import shared_menu


def deadline():
    return Deadline(expires_at_ns=time.monotonic_ns() + 10_000_000_000)


def context():
    k, menu, rt = shared_menu()
    inst = instantiate(menu, k, rt)
    singles = bind_candidate_materials(standalone_candidates(inst, k), inst)
    return SharedCandidateContext(
        instantiated=inst,
        group=GroupContext(knowledge=k, runtime=rt, menu=menu, allow_delegated_estimates=True),
        standalone=singles,
        deadline=deadline(),
    )


def test_one_shared_operation_preserves_two_material_ports_and_one_human():
    ctx = context()
    candidates = generate_shared_prep(ctx)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.duration_sec == 240
    assert len(candidate.covers) == 2
    assert len(candidate.resource_uses) == 1
    assert candidate.resource_uses[0].resource_id == "human_1"
    assert len(candidate.material_outputs) == 2
    assert len({m.spec_id for m in candidate.material_outputs}) == 2
    assert all(m.quantity is None for m in candidate.material_outputs)
    assert all(any(c.covers == (tid,) for c in ctx.standalone) for tid in candidate.covers)
    assert generate_shared_prep(ctx) == candidates


def test_without_development_permission_no_estimated_candidate():
    ctx = context()
    assert (
        generate_shared_prep(
            ctx.model_copy(
                update={"group": ctx.group.model_copy(update={"allow_delegated_estimates": False})}
            )
        )
        == ()
    )


def test_generation_uses_existing_deadline():
    ctx = context().model_copy(update={"deadline": Deadline(expires_at_ns=time.monotonic_ns() - 1)})
    with pytest.raises(TimeoutError):
        generate_shared_prep(ctx)


def test_selected_shared_candidate_covers_both_once_and_single_options_remain():
    ctx = context()
    shared = generate_shared_prep(ctx)
    base = ProblemCompiler().compile(
        ctx.group.knowledge,
        ctx.group.menu,
        ctx.group.runtime,
        SchedulingPolicy(policy_version="p3-test"),
        deadline(),
    )
    assert isinstance(base, SchedulingProblem)
    problem = base.model_copy(update={"shared_prep_candidates": shared})
    builder = ModelBuilder(problem, deadline())
    builder.build()
    builder.model.add(builder.selected[shared[0].carrier_id] == 1)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 3
    assert solver.solve(builder.model) in {cp_model.FEASIBLE, cp_model.OPTIMAL}
    for tid in shared[0].covers:
        assert (
            sum(
                solver.value(builder.selected[c.carrier_id])
                for c in builder.candidates
                if tid in c.covers
            )
            == 1
        )
        assert solver.value(builder.ends[tid]) - solver.value(builder.starts[tid]) == 240
    assert len({solver.value(builder.starts[t]) for t in shared[0].covers}) == 1
    # Force an overlapping second shared alternative: exact coverage must reject it.
    duplicate = shared[0].model_copy(update={"carrier_id": CarrierId("synthetic-overlap")})
    other = ModelBuilder(
        problem.model_copy(update={"shared_prep_candidates": (*shared, duplicate)}), deadline()
    )
    other.build()
    other.model.add(other.selected[shared[0].carrier_id] == 1)
    other.model.add(other.selected[duplicate.carrier_id] == 1)
    assert solver.solve(other.model) == cp_model.INFEASIBLE


def test_shared_plan_requires_source_rule_and_rejects_forged_duration_and_resources():
    from app.scheduling.solution_mapping import map_solution
    from app.validation.schedule import ScheduleValidator

    ctx = context()
    base = ProblemCompiler().compile(
        ctx.group.knowledge,
        ctx.group.menu,
        ctx.group.runtime,
        SchedulingPolicy(
            policy_version="p3-validation", shared_prep=True, allow_delegated_shared_estimates=True
        ),
        deadline(),
    )
    assert isinstance(base, SchedulingProblem)
    assert base.shared_prep_candidates
    builder = ModelBuilder(base, deadline())
    builder.build()
    chosen = base.shared_prep_candidates[0]
    builder.model.add(builder.selected[chosen.carrier_id] == 1)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 3
    assert solver.solve(builder.model) in {cp_model.OPTIMAL, cp_model.FEASIBLE}
    plan = map_solution(builder, solver)
    validator = ScheduleValidator()
    validation = validator.validate(ctx.group.knowledge, ctx.group.runtime, base, plan)
    assert validation.valid, validation.violations
    # IR mutation together with plan hash cannot override the authoritative rule.
    changed = chosen.model_copy(update={"duration_sec": 239})
    bad = base.model_copy(update={"shared_prep_candidates": (changed,)})
    assert not validator.validate(
        ctx.group.knowledge,
        ctx.group.runtime,
        bad,
        plan.model_copy(update={"problem_hash": bad.problem_hash}),
    ).valid
    changed = chosen.model_copy(update={"resource_uses": ()})
    bad = base.model_copy(update={"shared_prep_candidates": (changed,)})
    assignments = tuple(
        a.model_copy(update={"resource_uses": ()}) if a.carrier_id == chosen.carrier_id else a
        for a in plan.assignments
    )
    assert not validator.validate(
        ctx.group.knowledge,
        ctx.group.runtime,
        bad,
        plan.model_copy(update={"problem_hash": bad.problem_hash, "assignments": assignments}),
    ).valid


def test_greedy_evaluates_shared_alternative_without_forcing_it():
    from app.domain.compatibility import GroupRuleSpec
    from app.scheduling.greedy import GreedyScheduler
    from app.validation.schedule import ScheduleValidator

    ctx = context()
    # Explicit synthetic timing rule to isolate shared choice, not a measured claim.
    rules = []
    for rule in ctx.group.knowledge.rules:
        if rule.kind == "SHARED_PREP":
            spec = GroupRuleSpec.model_validate_json(rule.group_compatibility_predicate)
            rule = rule.model_copy(
                update={
                    "group_compatibility_predicate": spec.model_copy(
                        update={"duration_sec": 60}
                    ).model_dump_json()
                }
            )
        rules.append(rule)
    knowledge = ctx.group.knowledge.model_copy(update={"rules": tuple(rules)})
    problem = ProblemCompiler().compile(
        knowledge,
        ctx.group.menu,
        ctx.group.runtime,
        SchedulingPolicy(
            policy_version="synthetic-prep-saving",
            shared_prep=True,
            allow_delegated_shared_estimates=True,
        ),
        deadline(),
    )
    assert isinstance(problem, SchedulingProblem)
    result = GreedyScheduler().solve(problem, deadline())
    assert result.candidate is not None
    shared_ids = {c.carrier_id for c in problem.shared_prep_candidates}
    assert any(a.carrier_id in shared_ids for a in result.candidate.assignments)
    report = ScheduleValidator().validate(knowledge, ctx.group.runtime, problem, result.candidate)
    assert report.valid, report.violations


def test_late_member_preserves_standalone_choice_when_forced_sharing_is_worse():
    from app.scheduling.greedy import GreedyScheduler
    from app.scheduling.solution_mapping import map_solution
    from app.validation.schedule import ScheduleValidator

    ctx = context()
    base = ProblemCompiler().compile(
        ctx.group.knowledge,
        ctx.group.menu,
        ctx.group.runtime,
        SchedulingPolicy(
            policy_version="p3-late-synthetic-readiness",
            shared_prep=True,
            allow_delegated_shared_estimates=True,
        ),
        deadline(),
    )
    assert isinstance(base, SchedulingProblem)
    shared = base.shared_prep_candidates[0]
    late = shared.covers[1]
    problem = base.model_copy(
        update={
            "logical_tasks": tuple(
                t.model_copy(update={"earliest_start_sec": 7000}) if t.task_id == late else t
                for t in base.logical_tasks
            )
        }
    )
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 3
    opt = ModelBuilder(problem, deadline())
    opt.build()
    assert solver.solve(opt.model) == cp_model.OPTIMAL
    free_finish = solver.value(opt.makespan)
    assert not solver.boolean_value(opt.selected[shared.carrier_id])
    plan = map_solution(opt, solver)
    assert ScheduleValidator().validate(ctx.group.knowledge, ctx.group.runtime, problem, plan).valid
    forced = ModelBuilder(problem, deadline())
    forced.build()
    forced.model.add(forced.selected[shared.carrier_id] == 1)
    assert solver.solve(forced.model) == cp_model.OPTIMAL
    assert solver.value(forced.makespan) > free_finish
    greedy = GreedyScheduler().solve(problem, deadline())
    assert greedy.candidate is not None
    assert all(a.carrier_id != shared.carrier_id for a in greedy.candidate.assignments)
    assert (
        ScheduleValidator()
        .validate(ctx.group.knowledge, ctx.group.runtime, problem, greedy.candidate)
        .valid
    )
