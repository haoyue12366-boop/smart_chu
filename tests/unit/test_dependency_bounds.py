"""合成反例检查域冲突；候选最小时长用于下界，不用旧单独时长。"""

import pytest

from app.compiler.bounds import derive_bounds
from app.compiler.dependency_graph import build_dependency_graph
from app.domain.policy import SchedulingPolicy
from app.domain.scheduling_problem import CandidateCarrier, LogicalTask, TaskDependency
from tests.compiler_support import runtime
from tests.unit.test_knowledge_gate import fixture


def example(duration=600):
    recipe, _, _ = fixture(seconds=duration)
    tasks = tuple(
        LogicalTask(
            task_id=letter,
            recipe_instance_id="synthetic",
            operation_id="heat",
            operation=recipe.operations[0],
        )
        for letter in ("a", "b")
    )
    candidates = tuple(
        CandidateCarrier(
            carrier_id=letter, kind="STANDALONE", covers=(letter,), duration_sec=duration
        )
        for letter in ("a", "b")
    )
    dependencies = (TaskDependency(predecessor_id="a", successor_id="b"),)
    return tasks, candidates, dependencies


def test_cycle_and_dangling_edges_are_rejected_with_identity():
    tasks, _, edges = example()
    with pytest.raises(ValueError, match="环"):
        build_dependency_graph(
            tasks, (*edges, TaskDependency(predecessor_id="b", successor_id="a"))
        )
    with pytest.raises(ValueError, match="引用"):
        build_dependency_graph(tasks, (TaskDependency(predecessor_id="a", successor_id="missing"),))


def test_new_shorter_candidate_changes_safe_lower_bound_and_binding():
    tasks, candidates, edges = example()
    graph = build_dependency_graph(tasks, edges)
    policy = SchedulingPolicy(policy_version="synthetic", time_grid_sec=1)
    first = derive_bounds(graph, candidates, runtime(), policy)
    shorter = CandidateCarrier(
        carrier_id="shorter", kind="STANDALONE", covers=("a",), duration_sec=300
    )
    second = derive_bounds(graph, (*candidates, shorter), runtime(), policy)
    assert first.makespan_lower_bound_sec == 1200
    assert second.makespan_lower_bound_sec == 900
    assert first.input_hash != second.input_hash


def test_long_preparation_is_not_truncated_at_two_hours():
    tasks, candidates, edges = example(172800)
    bounds = derive_bounds(
        build_dependency_graph(tasks, edges),
        candidates,
        runtime(),
        SchedulingPolicy(policy_version="test", time_grid_sec=1),
    )
    assert bounds.horizon_sec >= 345600
    assert bounds.makespan_lower_bound_sec == 345600


def test_maximum_lag_propagates_late_successor_back_to_predecessor():
    tasks, candidates, _ = example(60)
    tasks = (tasks[0], tasks[1].model_copy(update={"earliest_start_sec": 600}))
    edges = (TaskDependency(predecessor_id="a", successor_id="b", max_lag_sec=0),)
    bounds = derive_bounds(
        build_dependency_graph(tasks, edges),
        candidates,
        runtime(),
        SchedulingPolicy(policy_version="test", time_grid_sec=1),
    )
    assert bounds.tasks[0].earliest_start_sec == 540
    impossible = (tasks[0].model_copy(update={"latest_end_sec": 500}), tasks[1])
    with pytest.raises(ValueError, match="时间域"):
        derive_bounds(
            build_dependency_graph(impossible, edges),
            candidates,
            runtime(),
            SchedulingPolicy(policy_version="test", time_grid_sec=1),
        )
