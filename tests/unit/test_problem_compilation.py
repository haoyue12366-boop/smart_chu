"""真实完整问题和合成运行冲突；设备与物料身份不能跨实例混淆。"""

import time

from app.compiler.compiler import ProblemCompiler
from app.domain.policy import SchedulingPolicy
from app.domain.ports import Deadline
from app.domain.reports import CompilationFailure
from tests.compiler_support import menu_for, published_knowledge, runtime


def compile_menu(*recipes, state=None, knowledge=None, policy=None):
    return ProblemCompiler().compile(
        knowledge or published_knowledge(),
        menu_for(*recipes),
        state or runtime(),
        policy or SchedulingPolicy(policy_version="p2-seconds-v1"),
        Deadline(expires_at_ns=time.monotonic_ns() + 10_000_000_000),
    )


def test_real_repeated_recipe_materials_are_separate_and_problem_is_immutable():
    knowledge = published_knowledge()
    recipe = knowledge.recipes[0]
    problem = compile_menu(recipe, recipe)
    assert not isinstance(problem, CompilationFailure), problem
    assert len(problem.logical_tasks) == 2 * len(recipe.operations)
    assert len(problem.material_flow.supplies) == 2 * len(
        {s.source_spec_id for s in problem.material_flow.supplies}
    )
    assert len({s.supply_id for s in problem.material_flow.supplies}) == len(
        problem.material_flow.supplies
    )
    by_id = {s.supply_id: s for s in problem.material_flow.supplies}
    tasks = {t.task_id: t for t in problem.logical_tasks}
    assert all(
        by_id[d.supply_id].recipe_instance_id == tasks[d.task_id].recipe_instance_id
        for d in problem.material_flow.demands
    )
    assert problem.policy.time_grid_sec == 1
    assert problem.policy.minute_output_mode == "DECIMAL"
    assert (
        type(problem).model_validate_json(problem.model_dump_json()).problem_hash
        == problem.problem_hash
    )
    assert all(
        tasks[e.predecessor_id].recipe_instance_id == tasks[e.successor_id].recipe_instance_id
        for e in problem.dependencies
    )


def test_full_recipe_program_and_explicit_device_alternatives_are_preserved():
    knowledge = published_knowledge()
    recipe = next(r for r in knowledge.recipes if r.name == "亲朋欢聚套餐")
    problem = compile_menu(recipe)
    assert not isinstance(problem, CompilationFailure), problem
    assert len(problem.mandatory_programs.programs) == 1
    assert len(problem.mandatory_programs.reservations) > 0
    assert problem.horizon_sec >= 14400


def test_unknown_or_unreleased_device_state_blocks_without_making_new_device():
    knowledge = published_knowledge()
    recipe = next(r for r in knowledge.recipes if r.name == "亲朋欢聚套餐")
    device = next(d for d in knowledge.devices if d.device_instance_id == "oven_1")
    from app.domain.runtime_snapshot import DeviceState

    state = runtime(
        device_states=(
            DeviceState(
                device_instance_id=device.device_instance_id,
                physical_resource_id=device.physical_resource_id,
                component_id=device.component_id,
                availability_status="UNKNOWN",
                occupancy_status="FREE",
                observed_at=runtime().time_origin.start_at,
                source="SIMULATED",
            ),
        )
    )
    result = compile_menu(recipe, state=state)
    assert isinstance(result, CompilationFailure)
    assert result.failure_class == "STATE_INCOMPLETE"


def test_expired_budget_and_incompatible_integer_minute_mode_are_explicit_failures():
    knowledge = published_knowledge()
    recipe = next(r for r in knowledge.recipes if r.name == "韩式泡菜鸦片鱼头")
    expired = ProblemCompiler().compile(
        knowledge,
        menu_for(recipe),
        runtime(),
        SchedulingPolicy(policy_version="p2"),
        Deadline(expires_at_ns=0),
    )
    assert isinstance(expired, CompilationFailure)
    assert expired.failure_class == "NO_SOLUTION_WITHIN_BUDGET"
    incompatible = compile_menu(
        recipe,
        policy=SchedulingPolicy(
            policy_version="legacy-minutes", time_grid_sec=60, minute_output_mode="INTEGER"
        ),
    )
    assert isinstance(incompatible, CompilationFailure)


def test_alias_of_unavailable_physical_device_cannot_bypass_state_block():
    from app.compiler.runtime_constraints import compile_resource_blocks
    from app.domain.runtime_snapshot import DeviceState

    knowledge = published_knowledge()
    oven = next(d for d in knowledge.devices if d.device_instance_id == "oven_1")
    alias = oven.model_copy(update={"device_instance_id": "oven-alias"})
    knowledge = knowledge.model_copy(update={"devices": (*knowledge.devices, alias)})
    state = runtime(
        device_states=(
            DeviceState(
                device_instance_id="oven-alias",
                physical_resource_id=oven.physical_resource_id,
                component_id=oven.component_id,
                availability_status="UNAVAILABLE",
                occupancy_status="FREE",
                observed_at=runtime().time_origin.start_at,
                source="SIMULATED",
                expected_recovery_at=runtime().time_origin.at(600),
            ),
        )
    )
    blocks = compile_resource_blocks(knowledge, state, {"oven_1"}, 1000)
    assert len(blocks) == 1
    assert blocks[0].interval.end_sec == 600


def test_reported_wrong_spec_stock_does_not_silently_fall_back_to_recipe_supply():
    from app.domain.runtime_snapshot import MaterialLot

    knowledge = published_knowledge()
    quantity = {"value": 1, "unit": "g", "scale": 1}
    zero = {"value": 0, "unit": "g", "scale": 1}
    stock = MaterialLot(
        lot_id="wrong-spec",
        spec_id="nonexistent-spec",
        produced_at=runtime().time_origin.start_at,
        quantity_produced=quantity,
        quantity_available=quantity,
        quantity_reserved=zero,
        storage_state="fresh",
        source_event_id="synthetic-stock",
        version=1,
        quality_status="QUALIFIED",
    )
    result = compile_menu(knowledge.recipes[0], state=runtime(material_lots=(stock,)))
    assert isinstance(result, CompilationFailure)
