"""合成容量谜题独立穷举；末段下界必须保留全部真实可行顺序。"""

from itertools import product

import pytest

from app.domain.canonical_recipe import CanonicalRecipeModel
from app.domain.objectives import ObjectiveStage
from app.domain.policy import ObjectiveSpec, SchedulingPolicy
from app.domain.recipe_context import RecipeSchedulingContext
from app.domain.reports import CompilationFailure
from app.domain.resources import DeviceInstance, DeviceProfile
from app.scheduling.cp_sat import CpSatScheduler
from app.scheduling.finish_spread_bounds import minimum_finish_spread
from app.scheduling.model_builder import ModelBuilder
from app.validation.schedule import ScheduleValidator
from tests.compiler_support import published_knowledge, runtime
from tests.unit.test_cp_sat_model import deadline
from tests.unit.test_problem_compilation import compile_menu


def terminal_menu(lengths=(5, 4, 3), units=(2, 1, 1), capacity=3, *, tail=False, shared=False):
    base = published_knowledge()
    evidence, rule = base.provenance_index[0].evidence_id, base.release.rule_version
    competition = "STATE_COMPATIBLE" if capacity > 1 or shared else "UNARY"
    profile = DeviceProfile(
        profile_id="synthetic-terminal-profile",
        device_type="synthetic",
        mode="test",
        constraints=({"parameter": "temperature_c", "minimum": 0, "maximum": 100},),
        rule_version=rule,
        provenance_refs=(evidence,),
    )
    device = DeviceInstance(
        device_instance_id="synthetic-terminal",
        physical_resource_id="synthetic-terminal",
        component_id="chamber",
        capacity=capacity,
        capability_refs=(profile.profile_id,),
        conflict_policy=competition,
        rule_version=rule,
        evidence_refs=(evidence,),
    )
    recipes, contexts = [], []
    for index, (length, amount) in enumerate(zip(lengths, units, strict=True)):
        use = {
            "resource_type": "DEVICE",
            "resource_id": device.device_instance_id,
            "physical_resource_id": device.physical_resource_id,
            "component_id": "chamber",
            "units": amount,
            "conflict_policy": competition,
            "rule_version": rule,
            "configuration": [
                {"parameter": "temperature_c", "value": 50},
                {"parameter": "mode", "value": "test"},
            ],
            "evidence_refs": [evidence],
        }
        if amount > 1:
            use["occupied_layer_indices"] = list(range(1, amount + 1))
        operations = [
            {
                "operation_id": "heat",
                "action": "HEAT",
                "duration": {"execution_sec": length - 1},
                "resource_requirements": [use],
            },
            {
                "operation_id": "out",
                "action": "UNLOAD",
                "duration": {"execution_sec": 1},
                "resource_requirements": [use],
            },
        ]
        dependencies = [
            {
                "predecessor_id": "heat",
                "successor_id": "out",
                "min_lag_sec": 0,
                "reason": "synthetic",
                "evidence_refs": [evidence],
            }
        ]
        if tail:
            operations.append(
                {
                    "operation_id": "later",
                    "action": "UNLOAD",
                    "duration": {"execution_sec": 1},
                    "resource_requirements": [],
                }
            )
            dependencies.append(
                {
                    "predecessor_id": "out",
                    "successor_id": "later",
                    "min_lag_sec": 0,
                    "reason": "synthetic",
                    "evidence_refs": [evidence],
                }
            )
        recipe = CanonicalRecipeModel(
            schema_version="1.0",
            recipe_id=f"synthetic-terminal-{index}",
            recipe_version="1",
            name=f"合成末段预约{index}",
            operations=operations,
            ingredient_requirements=(),
            material_specs=(),
            dependencies=dependencies,
            provenance_refs=(evidence,),
        )
        recipes.append(recipe)
        contexts.append(
            RecipeSchedulingContext.model_validate(
                {
                    "recipe_id": recipe.recipe_id.root,
                    "cooking_completion": {
                        "operation_ids": ["later" if tail else "out"],
                        "kind": "OUT_OF_POT",
                        "evidence_refs": [evidence],
                    },
                    "resource_reservations": [
                        {
                            "reservation_id": f"synthetic-terminal-span-{index}",
                            "members": ["heat", "out"],
                            "resource_options": [device.device_instance_id],
                            "policy": competition,
                            "span": "min_start_to_max_end",
                            "origin": "MODEL_SUGGESTION",
                        }
                    ],
                }
            )
        )
    knowledge = base.model_copy(
        update={
            "recipes": tuple(recipes),
            "devices": (device,),
            "profiles": (profile,),
            "device_choices": (),
            "recipe_contexts": tuple(contexts),
        }
    )
    policy = SchedulingPolicy(
        policy_version="synthetic-terminal-capacity",
        objective=ObjectiveSpec(
            stages=("SPREAD", "HUMAN_BUSY", "MAKESPAN"),
            spread_basis="COOKING_FINISH",
            spread_target_sec=0,
        ),
    )
    problem = compile_menu(*recipes, knowledge=knowledge, state=runtime(knowledge), policy=policy)
    assert not isinstance(problem, CompilationFailure), problem
    return knowledge, problem


def capacity_oracle(lengths, units, capacity):
    best = None
    horizon = sum(lengths)
    for starts in product(*(range(horizon - length + 1) for length in lengths)):
        ends = [start + length for start, length in zip(starts, lengths, strict=True)]
        if any(
            sum(
                amount
                for start, end, amount in zip(starts, ends, units, strict=True)
                if start <= second < end
            )
            > capacity
            for second in range(horizon)
        ):
            continue
        value = max(ends) - min(ends)
        best = value if best is None else min(best, value)
    return best


@pytest.mark.parametrize(
    "lengths,units,capacity,bound",
    [
        ((5, 4, 3), (2, 1, 1), 3, 3),
        ((5, 4, 3), (2, 2, 1), 3, 4),
        ((4, 3, 2), (1, 1, 1), 2, 2),
        ((5, 4, 3), (1, 1, 1), 3, 0),
        ((4, 3, 2), (1, 1, 1), 1, 3),
    ],
)
def test_terminal_capacity_bound_keeps_exhaustive_optimum(lengths, units, capacity, bound):
    knowledge, problem = terminal_menu(lengths, units, capacity)
    builder = ModelBuilder(problem, deadline())
    builder.build()
    assert builder.finish_spread_lower_bound_sec == bound
    expected = capacity_oracle(lengths, units, capacity)
    assert bound <= expected
    result = CpSatScheduler().solve(
        problem, None, deadline(), stage=ObjectiveStage(name="B_SPREAD")
    )
    assert result.status == "OPTIMAL", result
    assert result.objective_value == result.best_bound == expected
    proof = ScheduleValidator().validate(knowledge, problem.runtime, problem, result.candidate)
    assert proof.valid, proof.violations


def test_resource_reservation_ending_before_cooking_finish_cannot_bound_it():
    knowledge, problem = terminal_menu(tail=True)
    builder = ModelBuilder(problem, deadline())
    builder.build()
    assert builder.finish_spread_lower_bound_sec == 0
    result = CpSatScheduler().solve(
        problem, None, deadline(), stage=ObjectiveStage(name="B_SPREAD")
    )
    assert result.status == "OPTIMAL" and result.objective_value == 0
    assert ScheduleValidator().validate(knowledge, problem.runtime, problem, result.candidate).valid


def test_default_single_capacity_does_not_limit_compatible_shared_equipment():
    knowledge, problem = terminal_menu(units=(1, 1, 1), capacity=1, shared=True)
    builder = ModelBuilder(problem, deadline())
    builder.build()
    assert builder.finish_spread_lower_bound_sec == 0
    result = CpSatScheduler().solve(
        problem, None, deadline(), stage=ObjectiveStage(name="B_SPREAD")
    )
    assert result.status == "OPTIMAL" and result.objective_value == 0
    assert ScheduleValidator().validate(knowledge, problem.runtime, problem, result.candidate).valid


def test_actual_shortened_history_does_not_use_original_duration_bound():
    _, problem = terminal_menu()
    builder = ModelBuilder(problem, deadline())
    builder.build()
    first_heat = next(t.task_id for t in problem.logical_tasks if t.operation_id.root == "heat")
    builder.fixed[first_heat] = (0, 1)
    assert minimum_finish_spread(builder) == 0


def test_capacity_lower_bound_is_only_a_hint_when_it_is_unattainable(monkeypatch):
    from ortools.sat.python import cp_model

    knowledge, problem = terminal_menu((4, 3, 2), (1, 1, 1), 1)
    hints = []
    original = cp_model.CpSolver.solve

    def observe(solver, model, *args, **kwargs):
        hints.extend(
            (model.proto.variables[index].name, value)
            for index, value in zip(
                model.proto.solution_hint.vars, model.proto.solution_hint.values, strict=True
            )
        )
        return original(solver, model, *args, **kwargs)

    monkeypatch.setattr(cp_model.CpSolver, "solve", observe)
    result = CpSatScheduler().solve(
        problem, None, deadline(), stage=ObjectiveStage(name="B_SPREAD")
    )
    assert hints == [("spread-excess", 3)]
    assert result.status == "OPTIMAL"
    assert result.objective_value == result.best_bound == capacity_oracle((4, 3, 2), (1, 1, 1), 1)
    assert result.objective_value == 5
    assert ScheduleValidator().validate(knowledge, problem.runtime, problem, result.candidate).valid
