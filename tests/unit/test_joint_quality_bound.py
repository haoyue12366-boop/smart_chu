"""独立穷举联合目标；可行上界不固定尚未证明的高优先级分量。"""

from itertools import product

import pytest
from pydantic import ValidationError

from app.domain.objectives import ObjectiveStage
from app.scheduling.cp_sat import CpSatScheduler
from app.scheduling.metrics import compute_metrics
from tests.unit.test_total_human_objective import deadline, witness


def oracle(weight):
    scores = []
    for lengths in ((180,), (120, 120)):
        for starts in product(range(0, 361, 60), repeat=len(lengths)):
            spans = sorted(
                (start, start + length) for start, length in zip(starts, lengths, strict=True)
            )
            if spans[-1][1] > 360 or any(
                a[1] > b[0] for a, b in zip(spans, spans[1:], strict=False)
            ):
                continue
            origin, end, busy = spans[0][0], spans[0][1], 0
            for start, finish in spans[1:]:
                if start - end >= 60:
                    busy = max(busy, end - origin)
                    origin = start
                end = finish
            busy = max(busy, end - origin)
            # 两个无资源被动步骤可同时在0开始、240结束，所有完成差都<=300。
            scores.append((busy * weight + max(240, spans[-1][1]), busy, max(240, spans[-1][1])))
    return min(scores)


@pytest.mark.parametrize("offset", [-1, 0, 1000])
def test_joint_upper_bound_preserves_enumerated_optimum_and_rejects_impossible_cap(offset):
    problem = witness()
    objective = problem.policy.objective.model_copy(
        update={"stages": ("SPREAD", "HUMAN_BUSY", "MAKESPAN"), "spread_target_sec": 300}
    )
    problem = problem.model_copy(
        update={"policy": problem.policy.model_copy(update={"objective": objective})}
    )
    best, busy, makespan = oracle(problem.horizon_sec + 1)
    stage = ObjectiveStage(
        name="E_QUALITY", makespan_cap_sec=360, quality_upper_bound=best + offset
    )
    result = CpSatScheduler().solve(problem, None, deadline(), stage=stage)
    if offset < 0:
        assert result.status == "INFEASIBLE"
    else:
        assert result.status == "OPTIMAL", result
        assert result.objective_value == result.best_bound == best
        metrics = compute_metrics(result.candidate, problem)
        assert (metrics.max_continuous_human_sec, metrics.makespan_sec) == (busy, makespan)


def test_bound_survives_strict_json_job_contract_and_none_does_not_change_old_json():
    old = ObjectiveStage(name="E_QUALITY")
    assert "quality_upper_bound" not in old.model_dump(mode="json")
    stage = ObjectiveStage(name="E_QUALITY", quality_upper_bound=100)
    assert ObjectiveStage.model_validate_json(stage.model_dump_json()).quality_upper_bound == 100
    with pytest.raises(ValidationError):
        ObjectiveStage(name="C_MAKESPAN", quality_upper_bound=100)


def test_total_human_policy_cannot_apply_a_continuous_quality_bound():
    problem = witness()
    objective = problem.policy.objective.model_copy(
        update={"stages": ("SPREAD", "TOTAL_HUMAN_WORK", "MAKESPAN")}
    )
    problem = problem.model_copy(
        update={"policy": problem.policy.model_copy(update={"objective": objective})}
    )
    result = CpSatScheduler().solve(
        problem, None, deadline(), stage=ObjectiveStage(name="E_QUALITY", quality_upper_bound=100)
    )
    assert result.status == "MODEL_INVALID"


def test_engine_uses_current_verified_candidate_to_bound_positive_spread_stage():
    from app.scheduling.engine import PlanningEngine
    from app.scheduling.objectives import continuous_quality_value
    from app.validation.schedule import ScheduleValidator
    from tests.unit.test_prepared_greedy import prepared_menu

    knowledge, problem = prepared_menu()
    objective = problem.policy.objective.model_copy(
        update={
            "stages": ("SPREAD", "HUMAN_BUSY", "MAKESPAN"),
            "spread_target_sec": 0,
            "spread_basis": "COMPLETION",
        }
    )
    problem = problem.model_copy(
        update={"policy": problem.policy.model_copy(update={"objective": objective})}
    )
    solver = CpSatScheduler()
    calls = []

    class RecordingSolver:
        def solve(self, problem, hint, limit, *, stage=None, serial_menu=False):
            calls.append((stage, hint))
            return solver.solve(problem, hint, limit, stage=stage, serial_menu=serial_menu)

    result = PlanningEngine(validator=ScheduleValidator(), solver=RecordingSolver()).plan(
        problem, knowledge, problem.runtime, deadline()
    )
    assert result.status == "VALIDATED", result
    bounded = [
        (stage, hint) for stage, hint in calls if stage and stage.quality_upper_bound is not None
    ]
    assert bounded
    for stage, hint in bounded:
        assert stage.name == "E_QUALITY" and stage.spread_excess_cap_sec > 0
        assert hint is not None and hint.metrics is not None
        assert ScheduleValidator().validate(knowledge, problem.runtime, problem, hint).valid
        assert stage.quality_upper_bound == continuous_quality_value(problem, hint.metrics)


def test_json_worker_carries_joint_bound_to_native_model():
    from app.scheduling.json_worker import JsonSolverWorker
    from tests.unit.test_prepared_greedy import prepared_menu

    _, problem = prepared_menu()
    objective = problem.policy.objective.model_copy(
        update={"stages": ("SPREAD", "HUMAN_BUSY", "MAKESPAN")}
    )
    problem = problem.model_copy(
        update={"policy": problem.policy.model_copy(update={"objective": objective})}
    )
    with JsonSolverWorker() as worker:
        result = worker.solve(
            problem, None, deadline(), stage=ObjectiveStage(name="E_QUALITY", quality_upper_bound=0)
        )
        # 两个必需60秒人工操作给出与Solver独立的正下界，因此联合上界0无解。
        assert result.status == "INFEASIBLE", result
        assert worker.is_ready


def test_optional_upper_bound_is_omitted_when_integer_weights_are_unsafe():
    from app.domain.schedule import ScheduleMetrics
    from app.scheduling.objectives import continuous_quality_value
    from tests.unit.test_prepared_greedy import prepared_menu

    _, problem = prepared_menu()
    problem = problem.model_copy(update={"horizon_sec": 10**9})
    metrics = ScheduleMetrics(
        makespan_sec=5000,
        completion_spread_sec=5000,
        cooking_finish_spread_sec=5000,
        max_continuous_human_sec=1200,
    )
    assert continuous_quality_value(problem, metrics) is None


def test_complete_hint_above_joint_cap_is_not_used_to_bias_the_search():
    from app.scheduling.cp_sat import _hint_within_bounds

    problem = witness()
    objective = problem.policy.objective.model_copy(
        update={"stages": ("SPREAD", "HUMAN_BUSY", "MAKESPAN"), "spread_target_sec": 300}
    )
    problem = problem.model_copy(
        update={"policy": problem.policy.model_copy(update={"objective": objective})}
    )
    total = CpSatScheduler().solve(
        problem, None, deadline(), stage=ObjectiveStage(name="D_TOTAL_HUMAN", makespan_cap_sec=360)
    )
    metrics = compute_metrics(total.candidate, problem)
    hint = total.candidate.model_copy(update={"metrics": metrics})
    assert metrics.max_continuous_human_sec == 180  # 独立穷举证实共享路径只有一个180秒人工段。
    best, _, _ = oracle(problem.horizon_sec + 1)
    stage = ObjectiveStage(name="E_QUALITY", makespan_cap_sec=360, quality_upper_bound=best)
    assert not _hint_within_bounds(hint, problem, stage)
    assert not _hint_within_bounds(hint.model_copy(update={"metrics": None}), problem, stage)
