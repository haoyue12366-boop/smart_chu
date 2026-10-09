"""明确合成三层设备：同一工艺同时占第1、3层，仍只有一次执行。"""

import pytest

from app.domain.base import ReviewStatus
from app.domain.canonical_recipe import CanonicalRecipeModel
from app.domain.knowledge import EvidenceIndexEntry
from app.domain.policy import ObjectiveSpec, SchedulingPolicy
from app.domain.recipe_context import RecipeSchedulingContext
from app.domain.reports import CompilationFailure
from app.domain.resources import DeviceInstance, DeviceProfile, ResourceUse
from app.scheduling.cp_sat import CpSatScheduler
from app.scheduling.greedy import GreedyScheduler
from app.validation.schedule import ScheduleValidator
from tests.compiler_support import runtime
from tests.integration.test_schedule_clock import clock_knowledge, clock_service, tick
from tests.unit.test_cp_sat_model import deadline
from tests.unit.test_problem_compilation import compile_menu


def fixed_use():
    return ResourceUse.model_validate(
        {
            "resource_type": "DEVICE",
            "resource_id": "steam",
            "physical_resource_id": "steam",
            "component_id": "cavity",
            "conflict_policy": "STATE_COMPATIBLE",
            "units": 2,
            "occupied_layer_indices": [1, 3],
            "configuration": [
                {"parameter": "temperature_c", "value": 100},
                {"parameter": "mode", "value": "steam"},
            ],
        }
    )


def test_fixed_layer_set_preserves_one_resource_and_old_serialization():
    use = fixed_use()
    assert use.effective_layer_indices == (1, 3)
    assert use.units == 2
    assert use.layer_index is None
    legacy = ResourceUse(resource_type="DEVICE", resource_id="old")
    assert "occupied_layer_indices" not in legacy.model_dump()


@pytest.mark.parametrize(
    "changes",
    [
        {"occupied_layer_indices": [1, 1]},
        {"occupied_layer_indices": [3, 1]},
        {"units": 1},
        {"layer_index": 2},
    ],
)
def test_fixed_layer_set_rejects_ambiguous_or_inconsistent_requirements(changes):
    with pytest.raises(ValueError):
        ResourceUse.model_validate({**fixed_use().model_dump(), **changes})


def multilayer_knowledge(*, temperature=100):
    source = clock_knowledge(parallel=True)
    fixed = fixed_use().model_copy(
        update={
            "rule_version": source.release.rule_version,
            "evidence_refs": ("synthetic:clock",),
            "review_status": ReviewStatus.APPROVED,
        }
    )
    single = ResourceUse.model_validate(
        {
            **fixed.model_dump(exclude={"occupied_layer_indices"}),
            "units": 1,
            "configuration": [
                {"parameter": "temperature_c", "value": temperature},
                {"parameter": "mode", "value": "steam"},
            ],
        }
    )
    human = ResourceUse(resource_type="HUMAN", resource_id="human_1", conflict_policy="UNARY")
    recipes = tuple(
        CanonicalRecipeModel.model_validate(
            {
                "schema_version": "1.0",
                "recipe_id": f"multi-{index}",
                "recipe_version": "1",
                "name": f"合成双层{index}",
                "provenance_refs": ["synthetic:clock"],
                "ingredient_requirements": [],
                "material_specs": [],
                "operations": [
                    {
                        "operation_id": name,
                        "action": "LOAD" if name == "load" else "HEAT",
                        "duration": {"execution_sec": seconds},
                        "resource_requirements": [fixed, human]
                        if name == "load"
                        else [fixed if index == 0 else single],
                    }
                    for name, seconds in (
                        [("load", 30), ("heat", 60)] if index == 0 else [("heat", 120)]
                    )
                ],
                "dependencies": [
                    {
                        "predecessor_id": "load",
                        "successor_id": "heat",
                        "max_lag_sec": 0,
                        "reason": "合成装盘后连续加热",
                        "evidence_refs": ["synthetic:clock"],
                    }
                ]
                if index == 0
                else [],
            }
        )
        for index in range(2)
    )
    return source.model_copy(
        update={
            "recipes": recipes,
            "devices": (
                DeviceInstance(
                    device_instance_id="steam",
                    physical_resource_id="steam",
                    component_id="cavity",
                    capacity=3,
                    conflict_policy="STATE_COMPATIBLE",
                    capability_refs=("steam-profile",),
                    review_status="APPROVED",
                    rule_version=source.release.rule_version,
                    evidence_refs=("synthetic:clock",),
                ),
            ),
            "profiles": (
                DeviceProfile(
                    profile_id="steam-profile",
                    device_type="steam",
                    mode="steam",
                    review_status="APPROVED",
                    rule_version=source.release.rule_version,
                    provenance_refs=("synthetic:clock",),
                    constraints=({"parameter": "temperature_c", "minimum": 80, "maximum": 100},),
                ),
            ),
            "provenance_index": (
                EvidenceIndexEntry(
                    evidence_id="synthetic:clock",
                    artifact_path="synthetic-fixture",
                    artifact_hash="0" * 64,
                    locator="tests/unit/test_fixed_multilayer_resources.py",
                ),
            ),
            "recipe_contexts": (
                RecipeSchedulingContext.model_validate(
                    {
                        "recipe_id": "multi-0",
                        "resource_reservations": [
                            {
                                "reservation_id": "fixed-1-3",
                                "members": ["load", "heat"],
                                "resource_options": ["steam"],
                                "policy": "STATE_COMPATIBLE",
                                "span": "min_start_to_max_end",
                                "origin": "SOURCE_EXPLICIT",
                            }
                        ],
                    }
                ),
            ),
        }
    )


def multilayer_problem(*, temperature=100):
    knowledge = multilayer_knowledge(temperature=temperature)
    state = runtime(knowledge)
    problem = compile_menu(
        *knowledge.recipes,
        knowledge=knowledge,
        state=state,
        policy=SchedulingPolicy(
            policy_version="synthetic-multi", objective=ObjectiveSpec(stages=("MAKESPAN",))
        ),
    )
    assert not isinstance(problem, CompilationFailure), problem
    return knowledge, state, problem


@pytest.mark.parametrize("solver", ["cp", "greedy"])
@pytest.mark.parametrize("temperature,finish", [(100, 120), (90, 210)])
def test_solvers_reserve_both_layers_and_only_reuse_compatible_layer_two(
    solver, temperature, finish
):
    knowledge, state, problem = multilayer_problem(temperature=temperature)
    result = (
        CpSatScheduler().solve(problem, None, deadline())
        if solver == "cp"
        else GreedyScheduler().solve(problem, deadline())
    )
    assert result.candidate is not None, result
    candidate = result.candidate
    report = ScheduleValidator().validate(knowledge, state, problem, candidate)
    assert report.valid, report.violations
    assert len(candidate.assignments) == 3
    assert max(item.interval.end_sec for item in candidate.assignments) == finish
    assert (
        sum(
            use.resource_type == "HUMAN"
            for item in candidate.assignments
            for use in item.resource_uses
        )
        == 1
    )
    fixed_uses = [
        use
        for item in candidate.assignments
        for use in item.resource_uses
        if use.occupied_layer_indices
    ]
    assert len(fixed_uses) == 2
    assert all(use.effective_layer_indices == (1, 3) for use in fixed_uses)
    if temperature == 100:
        assert [
            use.layer_index
            for item in candidate.assignments
            for use in item.resource_uses
            if use.resource_type == "DEVICE" and not use.occupied_layer_indices
        ] == [2]


@pytest.mark.parametrize("blocked_layer", [1, 3])
def test_validator_rejects_reusing_either_fixed_layer(blocked_layer):
    knowledge, state, problem = multilayer_problem()
    candidate = CpSatScheduler().solve(problem, None, deadline()).candidate
    assignments = tuple(
        item.model_copy(
            update={
                "resource_uses": tuple(
                    use.model_copy(update={"layer_index": blocked_layer})
                    if use.resource_type == "DEVICE" and not use.occupied_layer_indices
                    else use
                    for use in item.resource_uses
                )
            }
        )
        for item in candidate.assignments
    )
    invalid = candidate.model_copy(update={"assignments": assignments})
    report = ScheduleValidator().validate(knowledge, state, problem, invalid)
    assert not report.valid
    assert any(item.code == "RESOURCE_LAYER" for item in report.violations)


def test_one_execution_atomically_acquires_hands_over_and_releases_both_layers(tmp_path):
    service, _, _ = clock_service(tmp_path, parallel=True, knowledge=multilayer_knowledge())
    first = tick(service, 10)
    active = [item for item in first.runtime.details.occupancies if item.released_at is None]
    double = [item for item in active if item.resource.occupied_layer_indices]
    assert len(double) == 1
    assert double[0].resource.effective_layer_indices == (1, 3)
    assert (
        sum(item.resource.units for item in active if item.resource.resource_type == "DEVICE") == 3
    )
    middle = tick(service, 30)
    held = [
        item for item in middle.runtime.details.occupancies if item.resource.occupied_layer_indices
    ]
    assert len([item for item in held if item.released_at is None]) == 1
    assert len({item.execution_id for item in held}) == 2
    assert all(item.resource.effective_layer_indices == (1, 3) for item in held)
    done = tick(service, 90)
    assert all(
        item.released_at is not None
        for item in done.runtime.details.occupancies
        if item.resource.occupied_layer_indices
    )
    still = [item for item in done.runtime.details.occupancies if item.released_at is None]
    assert len(still) == 1 and still[0].resource.layer_index == 2
    final = tick(service, 120)
    assert all(item.released_at is not None for item in final.runtime.details.occupancies)
    service.store.close()


def test_compiler_rejects_a_fixed_layer_outside_the_published_device():
    knowledge = multilayer_knowledge()
    changed = tuple(
        recipe.model_copy(
            update={
                "operations": tuple(
                    operation.model_copy(
                        update={
                            "resource_requirements": tuple(
                                use.model_copy(update={"occupied_layer_indices": (1, 4)})
                                if use.occupied_layer_indices
                                else use
                                for use in operation.resource_requirements
                            )
                        }
                    )
                    for operation in recipe.operations
                )
            }
        )
        for recipe in knowledge.recipes
    )
    knowledge = knowledge.model_copy(update={"recipes": changed})
    result = compile_menu(*changed, knowledge=knowledge, state=runtime(knowledge))
    assert isinstance(result, CompilationFailure)


def test_validator_rejects_changed_fixed_layers_and_incompatible_temperature():
    knowledge, state, problem = multilayer_problem()
    candidate = CpSatScheduler().solve(problem, None, deadline()).candidate
    for change in ("layers", "temperature"):
        assignments = tuple(
            item.model_copy(
                update={
                    "resource_uses": tuple(
                        use.model_copy(update={"occupied_layer_indices": (1, 2)})
                        if change == "layers" and use.occupied_layer_indices
                        else use.model_copy(
                            update={
                                "configuration": tuple(
                                    value.model_copy(update={"value": 90})
                                    if value.parameter == "temperature_c"
                                    else value
                                    for value in use.configuration
                                )
                            }
                        )
                        if change == "temperature" and use.layer_index == 2
                        else use
                        for use in item.resource_uses
                    )
                }
            )
            for item in candidate.assignments
        )
        report = ScheduleValidator().validate(
            knowledge, state, problem, candidate.model_copy(update={"assignments": assignments})
        )
        assert not report.valid
        assert any(issue.code == "RESOURCE_IDENTITY" for issue in report.violations)
        if change == "temperature":
            assert any(issue.code == "RESOURCE_CONFIGURATION" for issue in report.violations)


@pytest.mark.parametrize(
    "layer,temperature,blocked", [(1, 100, True), (3, 100, True), (2, 90, True), (2, 100, False)]
)
def test_independent_actual_occupancy_scan_preserves_both_layers(layer, temperature, blocked):
    from app.domain.runtime_facts import Occupancy as ActualOccupancy
    from app.domain.runtime_facts import RuntimeDetails
    from app.domain.runtime_snapshot import ExecutionRecord
    from app.domain.schedule import CandidateSchedule
    from app.domain.time import Interval
    from app.validation.actual_occupancy import check_actual_occupancy
    from app.validation.resources import Occupancy
    from app.validation.schedule_context import Scan

    knowledge, state, problem = multilayer_problem(temperature=temperature)
    occupied = ActualOccupancy(
        occupancy_id="synthetic-held-1-3",
        execution_id="previous",
        resource=fixed_use(),
        started_at=state.time_origin.at(0),
        awaiting_confirmation=True,
    )
    record = ExecutionRecord(
        execution_id="previous",
        task_ids=(),
        status="COMPLETED",
        source="SIMULATED",
        event_refs=("synthetic-event",),
        started_at=state.time_origin.at(0),
        finished_at=state.time_origin.at(0),
    )
    state = state.model_copy(
        update={"executions": (record,), "details": RuntimeDetails(occupancies=(occupied,))}
    )
    use = (
        knowledge.recipes[1]
        .operations[0]
        .resource_requirements[0]
        .model_copy(update={"layer_index": layer})
    )
    scan = Scan(
        knowledge,
        state,
        problem,
        CandidateSchedule(problem_hash=problem.problem_hash, assignments=()),
    )
    check_actual_occupancy(
        scan, [Occupancy(("steam", "cavity"), Interval(start_sec=0, end_sec=120), use, ())]
    )
    assert bool(scan.issues) is blocked


def test_partial_execution_replan_keeps_the_full_fixed_layer_set(tmp_path):
    from app.compiler.compiler import ProblemCompiler

    service, _, _ = clock_service(tmp_path, parallel=True, knowledge=multilayer_knowledge())
    current = tick(service, 10)
    problem = ProblemCompiler().compile(
        service.knowledge, current.menu, current.runtime, current.policy, deadline()
    )
    assert not isinstance(problem, CompilationFailure), problem
    facts = problem.fixed_executions
    candidate = CpSatScheduler().solve(problem, None, deadline()).candidate
    assert candidate is not None
    assert problem.fixed_executions == facts
    uses = [
        use
        for item in candidate.assignments
        for use in item.resource_uses
        if use.resource_type == "DEVICE"
    ]
    assert len(uses) == 1 and uses[0].effective_layer_indices == (1, 3)
    report = ScheduleValidator().validate(service.knowledge, current.runtime, problem, candidate)
    assert report.valid, report.violations
    service.store.close()
