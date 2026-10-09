"""合成精确数量与真实定性端口分别检验；计划余量不是实际库存。"""

from fractions import Fraction

import pytest

from app.compiler.material_flow import build_material_allocations, compile_material_flow
from app.compiler.shared_prep import generate_shared_prep
from app.domain.material import MaterialRequirement
from app.domain.material_flow import MaterialDemand, MaterialFlow, MaterialSupply
from app.domain.processing_rules import ProcessingRule
from app.domain.scheduling_problem import CandidateCarrier
from tests.unit.test_shared_prep_coverage import context


def exact(value, unit="g", spec="out", ident="requirement"):
    return MaterialRequirement(
        requirement_id=ident,
        spec_id=spec,
        quantity_kind="EXACT",
        quantity={"value": value, "unit": unit, "scale": 1},
    )


def synthetic_rules():
    return (
        ProcessingRule(
            rule_id="synthetic-reviewed-quantity",
            rule_version="synthetic",
            kind="SHARED_PREP",
            group_compatibility_predicate="SYNTHETIC quantity component test",
            evidence_refs=("synthetic-only",),
        ),
    )


def synthetic(demand=100, unit="g", loss=0):
    output = exact(300)
    supply = MaterialSupply(
        supply_id="out",
        recipe_instance_id="r",
        source_spec_id="out",
        requirement=output,
        producer_task_id="producer",
    )
    demands = [
        MaterialDemand(
            demand_id=f"d{i}",
            task_id=f"consumer{i}",
            supply_id="out",
            requirement=exact(demand, unit, ident=f"d{i}"),
            share_numerator=demand,
            share_denominator=300,
        )
        for i in range(2)
    ]
    if loss:
        demands.append(
            MaterialDemand(
                demand_id="loss",
                task_id="consumer0",
                supply_id="out",
                requirement=exact(loss, ident="loss"),
                share_numerator=loss,
                share_denominator=300,
                is_loss=True,
            )
        )
    carrier = CandidateCarrier(
        carrier_id="shared",
        kind="SHARED_PREP",
        covers=("producer", "other-producer"),
        duration_sec=60,
        material_outputs=(output,),
        rule_refs=("synthetic-reviewed-quantity",),
    )
    return carrier, MaterialFlow(supplies=(supply,), demands=tuple(demands))


def test_surplus_is_planned_and_loss_is_deducted():
    carrier, flow = synthetic(loss=10)
    model = build_material_allocations((carrier,), flow, synthetic_rules())
    assert len(model.allocations) == 3
    remaining = model.remainders[0]
    assert Fraction(remaining.share_numerator, remaining.share_denominator) == Fraction(90, 300)
    assert remaining.is_actual_inventory is False
    assert all(a.producer_carrier_id == carrier.carrier_id for a in model.allocations)


@pytest.mark.parametrize("amount,unit,loss", [(160, "g", 0), (100, "ml", 0), (100, "g", 101)])
def test_overallocation_loss_and_unit_mismatch_rejected(amount, unit, loss):
    carrier, flow = synthetic(amount, unit, loss)
    with pytest.raises(ValueError):
        build_material_allocations((carrier,), flow, synthetic_rules())


def test_real_shared_ports_keep_source_identity_and_qualitative_shares():
    ctx = context()
    carriers = generate_shared_prep(ctx)
    flow = compile_material_flow(ctx.instantiated, ctx.group.knowledge, ctx.group.runtime)
    model = build_material_allocations(carriers, flow, ctx.group.knowledge.rules)
    assert len({a.supply_id for a in model.allocations}) == 2
    assert all(a.share_numerator == a.share_denominator for a in model.allocations)
    assert all(not r.is_actual_inventory for r in model.remainders)
    assert ctx.group.runtime.material_lots == ()


def test_unselected_shared_candidate_produces_no_supply_and_tampering_is_rejected():
    from ortools.sat.python import cp_model

    from app.compiler.compiler import ProblemCompiler
    from app.domain.policy import SchedulingPolicy
    from app.domain.scheduling_problem import SchedulingProblem
    from app.scheduling.model_builder import ModelBuilder
    from app.scheduling.solution_mapping import map_solution
    from app.validation.schedule import ScheduleValidator
    from tests.unit.test_shared_prep_coverage import deadline

    ctx = context()
    problem = ProblemCompiler().compile(
        ctx.group.knowledge,
        ctx.group.menu,
        ctx.group.runtime,
        SchedulingPolicy(
            policy_version="p3-material", shared_prep=True, allow_delegated_shared_estimates=True
        ),
        deadline(),
    )
    assert isinstance(problem, SchedulingProblem)
    shared = problem.shared_prep_candidates[0]
    builder = ModelBuilder(problem, deadline())
    builder.build()
    builder.model.add(builder.selected[shared.carrier_id] == 0)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 3
    assert solver.solve(builder.model) in {cp_model.OPTIMAL, cp_model.FEASIBLE}
    amounts = [
        var
        for (carrier, _), var in builder.allocation_amounts.items()
        if carrier == shared.carrier_id
    ]
    assert amounts and all(solver.value(v) == 0 for v in amounts)
    plan = map_solution(builder, solver)
    validator = ScheduleValidator()
    assert validator.validate(ctx.group.knowledge, ctx.group.runtime, problem, plan).valid
    record = problem.material_allocations.allocations[0]
    changed = record.model_copy(update={"share_numerator": record.share_numerator + 1})
    corrupted = problem.model_copy(
        update={
            "material_allocations": problem.material_allocations.model_copy(
                update={"allocations": (changed, *problem.material_allocations.allocations[1:])}
            )
        }
    )
    assert not validator.validate(
        ctx.group.knowledge,
        ctx.group.runtime,
        corrupted,
        plan.model_copy(update={"problem_hash": corrupted.problem_hash}),
    ).valid
