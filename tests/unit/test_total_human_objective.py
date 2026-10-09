"""合成穷举见证：总人工更少与最长连续人工更短是不同的目标。"""

import itertools
import time

from app.compiler.compiler import ProblemCompiler
from app.domain.canonical_recipe import CanonicalRecipeModel
from app.domain.objectives import ObjectiveStage
from app.domain.ports import Deadline
from app.domain.runtime_facts import ActualResourceSpan, RuntimeDetails
from app.domain.runtime_snapshot import ExecutionRecord
from app.domain.scheduling_problem import CandidateCarrier, SchedulingProblem
from app.domain.time import Interval
from app.scheduling.cp_sat import CpSatScheduler
from app.scheduling.metrics import compute_metrics
from tests.compiler_support import menu_for, runtime
from tests.runtime_support import policy, synthetic_knowledge


def deadline():
    return Deadline(expires_at_ns=time.monotonic_ns() + 3_000_000_000)


def witness():
    knowledge = synthetic_knowledge()
    recipes = tuple(
        CanonicalRecipeModel.model_validate(
            recipe.model_copy(
                update={
                    "operations": (
                        recipe.operations[0].model_copy(
                            update={
                                "duration": recipe.operations[0].duration.model_copy(
                                    update={"execution_sec": 120}
                                )
                            }
                        ),
                        recipe.operations[1].model_copy(
                            update={
                                "duration": recipe.operations[1].duration.model_copy(
                                    update={"execution_sec": 240}
                                )
                            }
                        ),
                    ),
                    "dependencies": (),
                }
            ).model_dump()
        )
        for recipe in knowledge.recipes
    )
    knowledge = knowledge.model_copy(update={"recipes": recipes})
    problem = ProblemCompiler().compile(
        knowledge, menu_for(*recipes), runtime(knowledge), policy(), deadline()
    )
    assert isinstance(problem, SchedulingProblem), problem
    human = tuple(c for c in problem.standalone_candidates if c.resource_uses)
    # 仅为优化器的合成选项，不能通过真实知识的审核或发布。
    shared = CandidateCarrier(
        carrier_id="synthetic-shared-180",
        kind="SHARED_PREP",
        covers=tuple(c.covers[0] for c in human),
        duration_sec=180,
        resource_uses=human[0].resource_uses,
        rule_refs=("synthetic-objective-witness",),
    )
    return problem.model_copy(update={"shared_prep_candidates": (shared,)})


def enumerate_human_options():
    options = []
    for lengths in ((180,), (120, 120)):
        for starts in itertools.product(range(0, 361, 60), repeat=len(lengths)):
            spans = sorted(
                zip(starts, (s + n for s, n in zip(starts, lengths, strict=True)), strict=True)
            )
            if spans[-1][1] > 360 or any(
                a[1] > b[0] for a, b in zip(spans, spans[1:], strict=False)
            ):
                continue
            busy, first, end = 0, spans[0][0], spans[0][1]
            for start, finish in spans[1:]:
                if start - end >= 60:
                    busy = max(busy, end - first)
                    first = start
                end = finish
            options.append((sum(lengths), max(busy, end - first)))
    return options


def test_total_human_and_continuous_human_choose_different_carriers():
    problem = witness()
    options = enumerate_human_options()
    assert min(options) == (180, 180)
    assert min(options, key=lambda pair: pair[::-1]) == (240, 120)
    solver = CpSatScheduler()
    total = solver.solve(
        problem,
        None,
        deadline(),
        stage=ObjectiveStage(name="D_TOTAL_HUMAN", makespan_cap_sec=360),
    )
    assert total.status == "OPTIMAL", total
    assert total.objective_value == 180
    assert compute_metrics(total.candidate, problem).total_human_work_sec == 180
    busy = solver.solve(
        problem,
        None,
        deadline(),
        stage=ObjectiveStage(name="D_HUMAN", makespan_cap_sec=360),
    )
    assert busy.status == "OPTIMAL", busy
    assert busy.objective_value == 120
    assert compute_metrics(busy.candidate, problem).total_human_work_sec == 240


def test_later_busy_stage_cannot_worsen_total_human_bound():
    problem = witness()
    result = CpSatScheduler().solve(
        problem,
        None,
        deadline(),
        stage=ObjectiveStage(name="D_HUMAN", makespan_cap_sec=360, total_human_cap_sec=180),
    )
    assert result.status == "OPTIMAL", result
    assert result.objective_value == 180
    assert compute_metrics(result.candidate, problem).total_human_work_sec == 180


def test_failed_or_cancelled_menu_history_is_a_fixed_total_human_constant():
    problem = witness()
    use = problem.shared_prep_candidates[0].resource_uses[0]
    record = ExecutionRecord(
        execution_id="past-failed",
        task_ids=("retired-task",),
        status="FAILED",
        source="SIMULATED",
        event_refs=("past-start", "past-fail"),
        started_at=problem.runtime.time_origin.at(0),
        finished_at=problem.runtime.time_origin.at(50),
        resource_spans=(
            ActualResourceSpan(
                resource=use, interval=Interval(start_sec=0, end_sec=50), event_refs=("past-fail",)
            ),
        ),
    )
    state = problem.runtime.model_copy(
        update={
            "executions": (record,),
            "now_offset_sec": 50,
            "details": RuntimeDetails(retired_task_ids=("retired-task",), planning_kind="REPLAN"),
        }
    )
    problem = problem.model_copy(update={"runtime": state})
    result = CpSatScheduler().solve(
        problem, None, deadline(), stage=ObjectiveStage(name="D_TOTAL_HUMAN", makespan_cap_sec=410)
    )
    assert result.status == "OPTIMAL", result
    assert result.objective_value == 230
    metrics = compute_metrics(result.candidate, problem)
    assert metrics.actual_human_work_sec == 50
    assert metrics.remaining_human_work_sec == 180
    assert metrics.total_human_work_sec == 230
