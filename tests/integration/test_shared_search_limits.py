"""真实菜谱加显式开发热规则：限额不改变源工艺或独立基线。"""

from collections import Counter

import pytest

from app.compiler.compiler import ProblemCompiler
from app.domain.policy import SchedulingPolicy
from app.domain.scheduling_problem import SchedulingProblem
from app.scheduling.cp_sat import CpSatScheduler
from app.scheduling.greedy import GreedyScheduler
from app.validation.schedule import ScheduleValidator
from tests.unit.test_joint_thermal_batches import thermal_context
from tests.unit.test_shared_prep_coverage import deadline


def compile_limited(ctx, **settings):
    policy = SchedulingPolicy(
        policy_version="synthetic-limits-v1",
        shared_prep=True,
        strict_together_batch=True,
        allow_delegated_shared_estimates=True,
        **settings,
    )
    result = ProblemCompiler().compile(
        ctx.group.knowledge, ctx.group.menu, ctx.group.runtime, policy, deadline()
    )
    assert isinstance(result, SchedulingProblem), result
    return result


def repeated_context():
    from app.domain.ids import RecipeInstanceId

    ctx = thermal_context()
    menu = tuple(
        item.model_copy(update={"recipe_instance_id": RecipeInstanceId(f"repeat-{i}-{j}")})
        for i, item in enumerate(ctx.group.menu)
        for j in range(2)
    )
    return ctx.model_copy(update={"group": ctx.group.model_copy(update={"menu": menu})})


@pytest.mark.parametrize("per_demand,total", [(1, 256), (12, 2)])
def test_optional_limits_preserve_all_standalone_and_required_programs(per_demand, total):
    ctx = repeated_context()
    full = compile_limited(ctx)
    limited = compile_limited(
        ctx, max_nonstandalone_per_requirement=per_demand, max_nonstandalone_per_problem=total
    )
    optional = (*limited.shared_prep_candidates, *limited.thermal_batch_candidates)
    assert len(optional) <= total
    counts = Counter(t for c in optional for t in c.covers)
    assert max(counts.values()) <= per_demand
    assert limited.standalone_candidates == full.standalone_candidates
    assert limited.mandatory_programs == full.mandatory_programs
    assert limited.dependencies == full.dependencies
    assert limited.candidate_generation_report.candidate_truncated
    assert limited.candidate_generation_report.may_lose_optimum
    assert limited.candidate_generation_report.truncation_reasons


def test_retained_real_candidates_solve_without_claiming_full_search_optimum():
    ctx = thermal_context()
    problem = compile_limited(ctx, max_nonstandalone_per_problem=1)
    greedy = GreedyScheduler().solve(problem, deadline())
    assert greedy.candidate is not None
    solver = CpSatScheduler()
    result = solver.solve(problem, greedy.candidate, deadline())
    assert result.candidate is not None
    assert problem.candidate_generation_report.candidate_truncated
    assert result.optimal_for_retained_candidates_only
    assert result.objective_stage
    assert solver.last_build_report.candidate_truncated
    assert solver.last_build_report.pruning_counts["BUDGET_TRUNCATION"] >= 1
    assert (
        solver.last_build_report.selected_path_alternatives
        == problem.candidate_generation_report.retained_count
    )
    assert (
        ScheduleValidator()
        .validate(ctx.group.knowledge, ctx.group.runtime, problem, result.candidate)
        .valid
    )


def test_stream_stops_after_total_limit_without_enumerating_every_combination():
    from app.compiler.candidate_limits import retain_optional
    from app.compiler.shared_prep import generate_shared_prep
    from app.domain.ids import CarrierId
    from app.domain.pruning import PruningContext

    ctx = thermal_context()
    source = generate_shared_prep(ctx)[0]
    consumed = []

    def candidates():
        for i in range(10000):
            consumed.append(i)
            yield source.model_copy(
                update={
                    "carrier_id": CarrierId(f"candidate-{i}"),
                    "duration_sec": source.duration_sec + i,
                }
            )

    result = retain_optional(
        candidates(),
        SchedulingPolicy(policy_version="limit-test", max_nonstandalone_per_problem=2),
        deadline(),
        PruningContext(
            knowledge_version="synthetic",
            rule_version="synthetic",
            policy_version="limit-test",
            state_dependency_hash="synthetic",
        ),
    )
    assert len(result.candidates) == 2
    assert len(consumed) == 3
    assert not result.enumeration_complete
    assert result.truncation_reasons


def test_expired_shared_deadline_never_returns_partial_success():
    import time

    from app.domain.ports import Deadline
    from app.domain.reports import CompilationFailure

    ctx = thermal_context()
    result = ProblemCompiler().compile(
        ctx.group.knowledge,
        ctx.group.menu,
        ctx.group.runtime,
        SchedulingPolicy(policy_version="expired", shared_prep=True, strict_together_batch=True),
        Deadline(expires_at_ns=time.monotonic_ns() - 1),
    )
    assert isinstance(result, CompilationFailure)
    assert result.failure_class == "NO_SOLUTION_WITHIN_BUDGET"


def test_equivalent_stream_dedup_does_not_delete_shorter_non_equivalent_choice():
    from app.compiler.candidate_limits import retain_optional
    from app.compiler.shared_prep import generate_shared_prep
    from app.domain.ids import CarrierId
    from app.domain.pruning import PruningContext

    ctx = thermal_context()
    base = generate_shared_prep(ctx)[0]
    duplicate = base.model_copy(update={"carrier_id": CarrierId("equivalent-copy")})
    shorter = base.model_copy(
        update={
            "carrier_id": CarrierId("different-duration"),
            "duration_sec": base.duration_sec - 1,
        }
    )
    result = retain_optional(
        (base, duplicate, shorter),
        SchedulingPolicy(policy_version="dedup-test"),
        deadline(),
        PruningContext(
            knowledge_version="synthetic",
            rule_version="synthetic",
            policy_version="dedup-test",
            state_dependency_hash="synthetic",
        ),
    )
    assert result.candidates == (base, shorter)
    assert len(result.records) == 1 and result.records[0].kind == "EQUIVALENT"
    assert result.records[0].replacement_candidate_id == base.carrier_id
    assert result.enumeration_complete and not result.truncation_reasons


def test_switches_off_preserves_fixed_recipe_programs_even_with_small_limits():
    from app.domain.ids import RecipeInstanceId

    ctx = thermal_context()
    recipe = next(r for r in ctx.group.knowledge.recipes if r.name == "亲朋欢聚套餐")
    extra = ctx.group.menu[0].model_copy(
        update={
            "recipe_id": recipe.recipe_id,
            "name": recipe.name,
            "recipe_instance_id": RecipeInstanceId("mandatory-program"),
        }
    )
    ctx = ctx.model_copy(
        update={"group": ctx.group.model_copy(update={"menu": (*ctx.group.menu, extra)})}
    )
    limited = compile_limited(ctx, max_nonstandalone_per_problem=1)
    off = ProblemCompiler().compile(
        ctx.group.knowledge,
        ctx.group.menu,
        ctx.group.runtime,
        limited.policy.model_copy(update={"shared_prep": False, "strict_together_batch": False}),
        deadline(),
    )
    assert isinstance(off, SchedulingProblem)
    assert not off.shared_prep_candidates and not off.thermal_batch_candidates
    assert off.mandatory_programs == limited.mandatory_programs
    assert off.mandatory_programs.programs
    assert off.standalone_candidates == limited.standalone_candidates
    assert not off.candidate_generation_report.candidate_truncated


def test_soft_scale_limit_skips_costly_objectives_but_keeps_validated_hard_model():
    from app.domain.policy import ModelSize
    from app.scheduling.engine import PlanningEngine

    ctx = thermal_context()
    problem = compile_limited(ctx, model_soft_limits=ModelSize())
    assert problem.model_soft_limit_exceedances
    engine = PlanningEngine(validator=ScheduleValidator())
    result = engine.plan(problem, ctx.group.knowledge, ctx.group.runtime, deadline())
    assert result.status == "VALIDATED", result
    assert not result.human_objective_optimized and not result.stability_objective_optimized
    assert all(s.objective_stage not in {"D_HUMAN", "E_STABILITY"} for s in result.stage_results)
    assert result.validation.valid
