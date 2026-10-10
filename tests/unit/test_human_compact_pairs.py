"""更少布尔/线性约束的同一精确模型；完整提示仍允许改顺序。"""

import pytest

from app.scheduling import objectives
from app.scheduling.greedy import GreedyScheduler
from app.scheduling.human_hint import add_human_chain_hint
from app.scheduling.model_builder import ModelBuilder
from tests.unit.test_cp_sat_model import deadline
from tests.unit.test_human_rest_bounds import add_manual_phase, manual_builder
from tests.unit.test_prepared_greedy import prepared_menu


def test_unknown_pair_shares_one_gap_and_rest_boolean(monkeypatch):
    monkeypatch.setattr(objectives, "_COMPACT_HUMAN_PAIR_MIN_PHASES", 0, raising=False)
    builder, human = manual_builder()
    first = add_manual_phase(builder, human, 0, 6, 2)
    second = add_manual_phase(builder, human, 0, 6, 2)
    builder.model.add_no_overlap([first.interval, second.interval])
    objectives._human_busy(builder)
    names = {variable.name for variable in builder.model.proto.variables}
    assert "human-rest:0:1" in names
    assert "human-distance:0:1" in names
    assert not any(name.startswith("human-gap:") for name in names)


def test_compact_hint_uses_gap_in_actual_direction_for_both_orders(monkeypatch):
    monkeypatch.setattr(objectives, "_COMPACT_HUMAN_PAIR_MIN_PHASES", 0, raising=False)
    _, problem = prepared_menu()
    candidate = GreedyScheduler().solve(problem, deadline()).candidate
    assert candidate is not None
    builder = ModelBuilder(problem, deadline())
    builder.build()
    objectives._human_busy(builder)
    add_human_chain_hint(builder, candidate)
    hinted = {
        builder.model.proto.variables[index].name: value
        for index, value in zip(
            builder.model.proto.solution_hint.vars,
            builder.model.proto.solution_hint.values,
            strict=True,
        )
    }
    assignments = {item.carrier_id: item for item in candidate.assignments}
    checked = 0
    for variable in builder.model.proto.variables:
        if not variable.name.startswith("human-distance:"):
            continue
        _, first, second = variable.name.split(":")
        phases = (builder.human_intervals[int(first)], builder.human_intervals[int(second)])
        spans = [assignments[phase.carrier_id].interval for phase in phases]
        gap = max(spans[1].start_sec - spans[0].end_sec, spans[0].start_sec - spans[1].end_sec)
        assert hinted[variable.name] == gap
        assert hinted[f"human-rest:{first}:{second}"] == int(gap >= 60)
        checked += 1
    assert checked > 0


@pytest.mark.parametrize("spread_cap", [None, 1])
def test_unmet_spread_stage_keeps_original_search_representation(monkeypatch, spread_cap):
    from app.domain.objectives import ObjectiveStage

    monkeypatch.setattr(objectives, "_COMPACT_HUMAN_PAIR_MIN_PHASES", 0, raising=False)
    _, problem = prepared_menu()
    builder = ModelBuilder(problem, deadline())
    builder.build()
    objectives.apply_stage(
        builder, ObjectiveStage(name="E_QUALITY", spread_excess_cap_sec=spread_cap)
    )
    names = {variable.name for variable in builder.model.proto.variables}
    assert any(name.startswith("human-gap:") for name in names)
    assert not any(name.startswith(("human-rest:", "human-distance:")) for name in names)
