"""显式合成边界：只省已证明休息的连接，保留所有加工备选及真实顺序。"""

import pytest
from ortools.sat.python import cp_model

from app.domain.ids import CarrierId
from app.domain.inventory import InventoryLotClaim, InventorySupply
from app.domain.material import MaterialSpec
from app.domain.quantity import ScaledQuantity
from app.domain.scheduling_problem import CandidateCarrier, MemberTimeOffset, TaskDependency
from app.scheduling.human_chain_bounds import HumanChainBounds
from app.scheduling.model_builder import ModelBuilder
from app.scheduling.objectives import _human_busy
from app.scheduling.resource_model import ResourceInterval
from tests.unit.test_cp_sat_model import deadline
from tests.unit.test_prepared_greedy import prepared_menu


def operation(problem, name):
    return next(
        task
        for task in problem.logical_tasks
        if task.operation_id.root == name
        and task.recipe_instance_id == problem.recipe_instances[0].recipe_instance_id
    )


def pair(builder):
    heat = operation(builder.problem, "heat").task_id
    finish = operation(builder.problem, "finish").task_id
    return tuple(
        next(i for i, phase in enumerate(builder.human_intervals) if phase.tasks == (task,))
        for task in (heat, finish)
    )


def with_short_wait(problem, duration, *, member=False):
    cool = operation(problem, "cool").task_id
    original = next(c for c in problem.standalone_candidates if c.covers == (cool,))
    alternate = original.model_copy(
        update={
            "carrier_id": CarrierId("synthetic-short-wait"),
            "kind": "THERMAL_BATCH" if member else "STANDALONE",
            "duration_sec": duration + 30 if member else duration,
            "member_offsets": (
                MemberTimeOffset(
                    task_id=cool,
                    start_offset_sec=30,
                    end_offset_sec=30 + duration,
                ),
            )
            if member
            else (),
        }
    )
    field = "thermal_batch_candidates" if member else "standalone_candidates"
    return problem.model_copy(update={field: (*getattr(problem, field), alternate)})


def test_long_passive_dependency_removes_rest_link_without_fixing_other_dish_order():
    _, problem = prepared_menu()
    builder = ModelBuilder(problem, deadline())
    builder.build()
    first, second = pair(builder)
    bounds = HumanChainBounds(builder)
    assert bounds.must_rest(first, second)
    other = next(
        i
        for i, phase in enumerate(builder.human_intervals)
        if phase.tasks == (problem.logical_tasks[-1].task_id,)
    )
    assert not bounds.must_rest(first, other)
    assert bounds.can_follow(first, other) and bounds.can_follow(other, first)
    _human_busy(builder)
    names = {variable.name for variable in builder.model.proto.variables}
    assert f"human-gap:{first}:{second}" not in names
    assert f"human-before:{min(first, other)}:{max(first, other)}" in names


@pytest.mark.parametrize("duration,expected", [(0, False), (30, False), (59, False), (60, True)])
def test_rest_proof_uses_shortest_of_all_processing_options(duration, expected):
    _, problem = prepared_menu()
    problem = with_short_wait(problem, duration)
    builder = ModelBuilder(problem, deadline())
    builder.build()
    assert HumanChainBounds(builder).must_rest(*pair(builder)) is expected


def test_intermediate_duration_uses_member_projection_not_shared_outer_duration():
    _, problem = prepared_menu()
    problem = with_short_wait(problem, 30, member=True)
    builder = ModelBuilder(problem, deadline())
    builder.build()
    assert not HumanChainBounds(builder).must_rest(*pair(builder))


def test_dependency_lag_counts_toward_required_rest():
    _, problem = prepared_menu()
    problem = with_short_wait(problem, 30)
    cool = operation(problem, "cool").task_id
    problem = problem.model_copy(
        update={
            "dependencies": tuple(
                item.model_copy(update={"min_lag_sec": 30}) if item.predecessor_id == cool else item
                for item in problem.dependencies
            )
        }
    )
    builder = ModelBuilder(problem, deadline())
    builder.build()
    assert HumanChainBounds(builder).must_rest(*pair(builder))


def test_short_option_keeps_actual_continuous_busy_span_under_hard_windows():
    _, problem = prepared_menu()
    problem = with_short_wait(problem, 30)
    windows = (
        ({"heat": (0, 60), "cool": (60, 90), "finish": (90, 120)}),
        ({"heat": (300, 360), "cool": (360, 1560), "finish": (1560, 1590)}),
    )
    instances = {item.recipe_instance_id: i for i, item in enumerate(problem.recipe_instances)}
    tasks = []
    for task in problem.logical_tasks:
        if task.operation_id.root not in windows[instances[task.recipe_instance_id]]:
            tasks.append(task)  # 已满足的前置声明保持原事实和时长。
            continue
        lower, upper = windows[instances[task.recipe_instance_id]][task.operation_id.root]
        tasks.append(task.model_copy(update={"earliest_start_sec": lower, "latest_end_sec": upper}))
    builder = ModelBuilder(problem.model_copy(update={"logical_tasks": tuple(tasks)}), deadline())
    builder.build()
    maximum = _human_busy(builder)
    builder.model.minimize(maximum)
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    assert solver.solve(builder.model) == cp_model.OPTIMAL
    # 第一菜60秒操作、30秒短间隔、30秒收尾组成120秒真实块；第二菜隔开。
    assert solver.objective_value == 120
    builder.model.add(maximum <= 119)
    assert solver.solve(builder.model) == cp_model.INFEASIBLE


@pytest.mark.parametrize("kind", ["inventory", "fixed"])
def test_supply_conditional_edges_are_never_used_as_unconditional_rest(kind):
    _, problem = prepared_menu()
    heat, cool = operation(problem, "heat"), operation(problem, "cool")
    # 这里只检验图边分类，不以人工构造的供应事实作为合法发布见证。
    if kind == "inventory":
        quantity = ScaledQuantity(value=100, unit="g", scale=1)
        supply = InventorySupply(
            rule_id="synthetic-rest-supply",
            target_supply_id="synthetic-target",
            target_spec=MaterialSpec(
                spec_id="synthetic", ingredient_id="rice", name="synthetic", state="cooled"
            ),
            quantity=quantity,
            lot_claims=(InventoryLotClaim(lot_id="synthetic-lot", quantity=quantity),),
            available_at_sec=0,
        )
        carrier = CandidateCarrier(
            carrier_id="synthetic-rest-supply",
            kind="INVENTORY_SUPPLY",
            covers=(heat.task_id, cool.task_id),
            duration_sec=0,
            inventory_supply=supply,
        )
        update = {"inventory_supply_candidates": (carrier,)}
    else:
        item = problem.advance_preparations[0].model_copy(
            update={
                "task_ids": (heat.task_id, cool.task_id),
                "operation_ids": (heat.operation_id, cool.operation_id),
            }
        )
        update = {"advance_preparations": (item,)}
    builder = ModelBuilder(problem, deadline())
    builder.build()
    builder.problem = problem.model_copy(update=update)
    assert not HumanChainBounds(builder).must_rest(*pair(builder))


def test_cyclic_graph_keeps_unknown_rest_instead_of_inventing_path_duration():
    _, problem = prepared_menu()
    builder = ModelBuilder(problem, deadline())
    builder.build()
    # 人工合成循环只测试保守界；不作为可行调度或正式知识。
    reverse = TaskDependency(
        predecessor_id=operation(problem, "finish").task_id,
        successor_id=operation(problem, "heat").task_id,
    )
    builder.problem = problem.model_copy(update={"dependencies": (*problem.dependencies, reverse)})
    assert not HumanChainBounds(builder).must_rest(*pair(builder))


def manual_builder(rest=3):
    _, problem = prepared_menu()
    objective = problem.policy.objective.model_copy(update={"rest_gap_sec": rest})
    builder = ModelBuilder(
        problem.model_copy(
            update={
                "policy": problem.policy.model_copy(update={"objective": objective}),
            }
        ),
        deadline(),
    )
    human = next(
        use
        for carrier in problem.standalone_candidates
        for use in carrier.resource_uses
        if use.resource_type == "HUMAN"
    )
    return builder, human


def add_manual_phase(builder, human, start_min, start_max, duration, **metadata):
    model = builder.model
    identity = len(builder.human_intervals)
    start = model.new_int_var(start_min, start_max, f"work-start-{identity}")
    end = model.new_int_var(start_min + duration, start_max + duration, f"work-end-{identity}")
    always = model.new_constant(1)
    interval = model.new_interval_var(start, duration, end, f"work-{identity}")
    phase = ResourceInterval(
        ("human_1", "human_1"), start, end, interval, always, human, (), **metadata
    )
    builder.human_intervals.append(phase)
    return phase


@pytest.mark.parametrize("gap,expected", [(2, False), (3, True)])
def test_exact_window_or_history_rest_threshold(gap, expected):
    builder, human = manual_builder()
    add_manual_phase(builder, human, 0, 0, 2)
    add_manual_phase(builder, human, 2 + gap, 5 + gap, 2)
    assert HumanChainBounds(builder).must_rest(0, 1) is expected


def test_other_human_work_can_bridge_an_omitted_long_gap_pair():
    builder, human = manual_builder()
    first = add_manual_phase(builder, human, 0, 0, 2)
    last = add_manual_phase(builder, human, 6, 6, 2)
    middle = add_manual_phase(builder, human, 2, 2, 4)
    builder.model.add_no_overlap([first.interval, middle.interval, last.interval])
    assert HumanChainBounds(builder).must_rest(0, 1)
    maximum = _human_busy(builder)
    builder.model.minimize(maximum)
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    assert solver.solve(builder.model) == cp_model.OPTIMAL
    assert solver.objective_value == 8
    builder.model.add(maximum <= 7)
    assert solver.solve(builder.model) == cp_model.INFEASIBLE


@pytest.mark.parametrize("gap,expected", [(2, False), (3, True)])
def test_same_multimember_carrier_rest_uses_exact_phase_offsets(gap, expected):
    builder, human = manual_builder()
    covers = (
        operation(builder.problem, "heat").task_id,
        operation(builder.problem, "finish").task_id,
    )
    carrier = CandidateCarrier(
        carrier_id="synthetic-shared-human",
        kind="THERMAL_BATCH",
        covers=covers,
        duration_sec=10,
        member_offsets=tuple(
            MemberTimeOffset(task_id=task, start_offset_sec=0, end_offset_sec=10) for task in covers
        ),
    )
    builder.candidates = (carrier,)
    add_manual_phase(
        builder, human, 0, 0, 2, carrier_id=carrier.carrier_id, start_offset_sec=0, end_offset_sec=2
    )
    add_manual_phase(
        builder,
        human,
        2 + gap,
        2 + gap,
        2,
        carrier_id=carrier.carrier_id,
        start_offset_sec=2 + gap,
        end_offset_sec=4 + gap,
    )
    bounds = HumanChainBounds(builder)
    assert bounds.phase_tasks == [None, None]
    assert bounds.must_rest(0, 1) is expected


@pytest.mark.parametrize("starts,expected", [((0, 5), 2), ((2, 0), 4)])
def test_proven_rest_direction_still_binds_order_when_reverse_can_be_short(
    monkeypatch, starts, expected
):
    builder, human = manual_builder()
    first = add_manual_phase(builder, human, 0, 6, 2)
    second = add_manual_phase(builder, human, 0, 6, 2)
    model = builder.model
    model.add_no_overlap([first.interval, second.interval])
    actual_order = model.new_bool_var("actual-order")
    model.add(second.start - first.end >= 3).only_enforce_if(actual_order)
    model.add(first.start - second.end >= 0).only_enforce_if(actual_order.negated())
    # 边界替身仅提供真实约束已支持的条件证明；两个未来顺序都合法。
    monkeypatch.setattr(
        HumanChainBounds,
        "must_rest",
        lambda self, left, right: (left, right) == (0, 1),
        raising=False,
    )
    model.add(first.start == starts[0])
    model.add(second.start == starts[1])
    maximum = _human_busy(builder)
    model.minimize(maximum)
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    assert solver.solve(model) == cp_model.OPTIMAL
    assert solver.objective_value == expected
    model.add(maximum < expected)
    assert solver.solve(model) == cp_model.INFEASIBLE
