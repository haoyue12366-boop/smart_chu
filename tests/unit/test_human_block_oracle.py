"""小时间域独立穷举全部开始时刻和可选加工，核对连续人工目标。"""

from itertools import product

import pytest
from ortools.sat.python import cp_model

from app.scheduling.model_builder import ModelBuilder
from app.scheduling.objectives import _human_busy
from app.scheduling.resource_model import ResourceInterval
from tests.unit.test_cp_sat_model import deadline
from tests.unit.test_prepared_greedy import prepared_menu


def enumerated_optimum(durations, earliest, latest, rest, optional):
    best = None
    masks = ((True, True, False), (True, False, True)) if optional else ((True, True, True),)
    for mask in masks:
        for starts in product(
            *(range(a, b - d + 1) for a, b, d in zip(earliest, latest, durations, strict=True))
        ):
            spans = sorted(
                (start, start + duration)
                for start, duration, present in zip(starts, durations, mask, strict=True)
                if present
            )
            if any(a[1] > b[0] for a, b in zip(spans, spans[1:], strict=False)):
                continue
            longest, origin, last_end = 0, None, None
            for start, end in spans:
                if last_end is None or start - last_end >= rest:
                    origin = start
                longest = max(longest, end - origin)
                last_end = end
            best = longest if best is None else min(best, longest)
    return best


@pytest.mark.parametrize(
    "earliest,latest,rest,optional",
    [
        ((0, 0, 0), (7, 7, 7), 2, False),
        ((0, 0, 0), (8, 8, 8), 2, False),
        ((0, 3, 5), (2, 6, 8), 2, False),
        ((0, 0, 0), (5, 5, 5), 0, False),
        ((0, 0, 0), (7, 7, 7), 1, False),
        ((0, 0, 0), (8, 8, 8), 20, False),
        ((0, 0, 0), (4, 4, 4), 2, False),
        ((0, 0, 0), (6, 6, 6), 2, True),
        ((0, 3, 3), (2, 6, 6), 2, True),
    ],
)
def test_human_objective_matches_full_enumeration(earliest, latest, rest, optional):
    _, problem = prepared_menu()
    objective = problem.policy.objective.model_copy(update={"rest_gap_sec": rest})
    problem = problem.model_copy(
        update={"policy": problem.policy.model_copy(update={"objective": objective})}
    )
    builder = ModelBuilder(problem, deadline())
    model = builder.model
    durations = (2, 1, 2)
    human = next(
        use
        for carrier in problem.standalone_candidates
        for use in carrier.resource_uses
        if use.resource_type == "HUMAN"
    )
    present = [model.new_constant(1)] + [
        model.new_bool_var(f"option-{i}") if optional else model.new_constant(1) for i in range(2)
    ]
    if optional:
        model.add(present[1] + present[2] == 1)
    for index, (duration, lower, upper) in enumerate(zip(durations, earliest, latest, strict=True)):
        start = model.new_int_var(lower, upper - duration, f"start-{index}")
        end = model.new_int_var(lower + duration, upper, f"end-{index}")
        interval = model.new_optional_interval_var(
            start, duration, end, present[index], f"work-{index}"
        )
        builder.human_intervals.append(
            ResourceInterval(
                ("human_1", "human_1"), start, end, interval, present[index], human, ()
            )
        )
    model.add_no_overlap([phase.interval for phase in builder.human_intervals])
    model.minimize(_human_busy(builder))
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    solver.parameters.max_time_in_seconds = 2
    status = solver.solve(model)
    expected = enumerated_optimum(durations, earliest, latest, rest, optional)
    if expected is None:
        assert status == cp_model.INFEASIBLE
    else:
        assert status == cp_model.OPTIMAL
        assert solver.objective_value == expected
