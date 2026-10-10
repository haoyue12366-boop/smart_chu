"""合成互斥人工备选；小时间域独立穷举及真实 Hint 检查。"""

from dataclasses import replace
from itertools import product

import pytest
from ortools.sat.python import cp_model

from app.domain.ids import CarrierId
from app.scheduling.human_chain_bounds import HumanChainBounds
from app.scheduling.human_hint import add_human_chain_hint
from app.scheduling.human_objective_groups import HumanObjectiveGroups
from app.scheduling.model_builder import ModelBuilder
from app.scheduling.objectives import _human_busy
from app.scheduling.resource_model import ResourceInterval
from tests.unit.test_cp_sat_model import deadline
from tests.unit.test_prepared_greedy import prepared_menu


def grouped_case(rest, *, no_human_option=False):
    _, problem = prepared_menu()
    sources = [c for c in problem.standalone_candidates if c.resource_uses]
    first = sources[0]
    other = next(c for c in sources if not set(c.covers) & set(first.covers))
    carriers = (
        first.model_copy(update={"carrier_id": CarrierId("synthetic:choice-a"), "duration_sec": 1}),
        first.model_copy(update={"carrier_id": CarrierId("synthetic:choice-b"), "duration_sec": 2}),
        other.model_copy(update={"carrier_id": CarrierId("synthetic:other"), "duration_sec": 2}),
    )
    objective = problem.policy.objective.model_copy(update={"rest_gap_sec": rest})
    problem = problem.model_copy(
        update={
            "policy": problem.policy.model_copy(update={"objective": objective}),
            "standalone_candidates": carriers,
            "dependencies": (),
        }
    )
    builder = ModelBuilder(problem, deadline())
    model = builder.model
    choices = [model.new_bool_var(f"choice-{i}") for i in range(2)]
    model.add(sum(choices) <= 1 if no_human_option else sum(choices) == 1)
    start, end = model.new_int_var(0, 5, "choice-start"), model.new_int_var(0, 7, "choice-end")
    human = first.resource_uses[0]
    for i, carrier in enumerate(carriers):
        present = choices[i] if i < 2 else model.new_constant(1)
        left = start if i < 2 else model.new_int_var(0, 5, "other-start")
        right = end if i < 2 else model.new_int_var(2, 7, "other-end")
        interval = model.new_optional_interval_var(
            left, carrier.duration_sec, right, present, f"work-{i}"
        )
        builder.selected[carrier.carrier_id] = present
        builder.human_intervals.append(
            ResourceInterval(
                ("human_1", "human_1"),
                left,
                right,
                interval,
                present,
                human,
                carrier.covers,
                carrier_id=carrier.carrier_id,
                end_offset_sec=carrier.duration_sec,
            )
        )
    model.add_no_overlap([p.interval for p in builder.human_intervals])
    return builder


def oracle(rest, no_human_option):
    best = 100
    for duration in (0, 1, 2) if no_human_option else (1, 2):
        for first, other in product(range(6), repeat=2):
            spans = sorted([(other, other + 2)] + ([(first, first + duration)] if duration else []))
            if len(spans) == 2 and spans[0][1] > spans[1][0]:
                continue
            value = max(b - a for a, b in spans)
            if len(spans) == 2 and spans[1][0] - spans[0][1] < rest:
                value = spans[1][1] - spans[0][0]
            best = min(best, value)
    return best


@pytest.mark.parametrize("rest", [0, 1, 2, 8])
@pytest.mark.parametrize("no_human_option", [False, True])
def test_mutually_exclusive_phases_share_one_objective_span(rest, no_human_option):
    builder = grouped_case(rest, no_human_option=no_human_option)
    original = tuple(builder.human_intervals)
    maximum = _human_busy(builder)
    builder.model.minimize(maximum)
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    assert solver.solve(builder.model) == cp_model.OPTIMAL
    assert solver.objective_value == oracle(rest, no_human_option)
    # 正确性之外，确认原资源/候选完整保留，人工目标只表示实际在场的一个备选。
    assert tuple(builder.human_intervals) == original
    assert sum(v.name.startswith("human-span:") for v in builder.model.proto.variables) == 2


def test_real_complete_hint_supports_either_equivalent_equipment_option():
    from app.scheduling.greedy import GreedyScheduler

    _, problem = prepared_menu()
    source = next(c for c in problem.standalone_candidates if c.resource_uses)
    alternative = source.model_copy(update={"carrier_id": CarrierId("synthetic:equipment-option")})
    problem = problem.model_copy(
        update={"standalone_candidates": (*problem.standalone_candidates, alternative)}
    )
    candidate = GreedyScheduler().solve(problem, deadline()).candidate
    assert candidate is not None
    for chosen in (source.carrier_id, alternative.carrier_id):
        candidate = candidate.model_copy(
            update={
                "assignments": tuple(
                    a.model_copy(update={"carrier_id": chosen})
                    if a.carrier_id in (source.carrier_id, alternative.carrier_id)
                    else a
                    for a in candidate.assignments
                )
            }
        )
        builder = ModelBuilder(problem, deadline())
        builder.build()
        maximum = _human_busy(builder)
        add_human_chain_hint(builder, candidate)
        builder.model.minimize(maximum)
        solver = cp_model.CpSolver()
        solver.parameters.num_search_workers = 1
        solver.parameters.fix_variables_to_their_hinted_value = True
        assert solver.solve(builder.model) == cp_model.OPTIMAL
        assert solver.objective_value == candidate.metrics.max_continuous_human_sec
        assert (
            sum(v.name.startswith("human-span:") for v in builder.model.proto.variables)
            == len(builder.human_intervals) - 1
        )


@pytest.mark.parametrize(
    "case", ["offset", "coverage", "history", "unbound", "same-carrier", "zero"]
)
def test_grouping_requires_identical_variables_and_proven_exclusive_positive_work(case):
    builder = grouped_case(2)
    phase = builder.human_intervals[1]
    if case == "offset":
        phase = replace(phase, start=builder.model.new_int_var(0, 5, "distinct-offset-start"))
    elif case == "coverage":
        phase = replace(phase, tasks=builder.human_intervals[2].tasks)
    elif case == "history":
        phase = replace(phase, carrier_id=None)
    elif case == "unbound":
        phase = replace(phase, presence=builder.model.new_bool_var("not-carrier-selection"))
    elif case == "same-carrier":
        phase = replace(phase, carrier_id=builder.human_intervals[0].carrier_id)
    else:
        phase = replace(
            phase,
            interval=builder.model.new_optional_interval_var(
                phase.start, 0, phase.end, phase.presence, "synthetic-zero-phase"
            ),
        )
    builder.human_intervals[1] = phase
    groups = HumanObjectiveGroups(builder, HumanChainBounds(builder))
    assert len(groups.phases) == 3


@pytest.mark.parametrize("rest", [0, 1, 2, 3, 8])
def test_grouped_alternative_can_bridge_two_fixed_history_segments(rest):
    from tests.unit.test_human_rest_bounds import add_manual_phase

    builder = grouped_case(rest)
    # 一段选中的人工长度可变，固定历史/在途段只作为原资源事实携入。
    builder.model.add(builder.human_intervals[0].start == 2)
    builder.model.add(builder.human_intervals[2].start == 5)
    human = builder.human_intervals[0].use
    add_manual_phase(builder, human, 0, 0, 2)
    builder.model.add_no_overlap([p.interval for p in builder.human_intervals])
    maximum = _human_busy(builder)
    builder.model.minimize(maximum)
    expected = 100
    for duration in (1, 2):
        spans = [(0, 2), (2, 2 + duration), (5, 7)]
        origin, longest, prior = 0, 0, None
        for start, end in spans:
            if prior is None or start - prior >= rest:
                origin = start
            longest = max(longest, end - origin)
            prior = end
        expected = min(expected, longest)
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    assert solver.solve(builder.model) == cp_model.OPTIMAL
    assert solver.objective_value == expected
    builder.model.add(maximum < expected)
    assert solver.solve(builder.model) == cp_model.INFEASIBLE
