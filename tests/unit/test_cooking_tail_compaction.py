"""合成可行反例：限时求解留下的收尾空档不能原样发布。"""

from app.domain.reports import SolveResult
from app.domain.time import Interval
from app.scheduling.engine import PlanningEngine
from app.scheduling.metrics import compute_metrics
from app.scheduling.tail_compaction import compact_cooking_tails
from app.validation.schedule import ScheduleValidator
from tests.unit.test_cooking_completion import (
    allow_late_plating,
    cooking_case,
    plated_candidate,
)
from tests.unit.test_cp_sat_model import deadline


def test_engine_compacts_feasible_cooking_tails_before_returning():
    knowledge, problem = cooking_case()
    problem = allow_late_plating(problem)
    original = plated_candidate(problem)
    # 两菜出锅分别为600/1800秒；收尾故意延后，但仍短于合法串行参考2340秒。
    original = original.model_copy(
        update={
            "assignments": tuple(
                assignment.model_copy(
                    update={
                        "interval": Interval(
                            start_sec=assignment.interval.start_sec - 1380,
                            end_sec=assignment.interval.end_sec - 1380,
                        )
                    }
                )
                if assignment.interval.start_sec >= 3540
                else assignment
                for assignment in original.assignments
            )
        }
    )
    assert ScheduleValidator().validate(knowledge, problem.runtime, problem, original).valid

    class FeasibleSolver:
        def solve(self, problem, hint, deadline, **kwargs):
            # 替身只固定外部求解结果；真实编排、指标和独立校验仍运行。
            return SolveResult(
                status="FEASIBLE",
                problem_hash=problem.problem_hash,
                candidate=original,
                objective_stage=kwargs["stage"].name,
            )

    result = PlanningEngine(validator=ScheduleValidator(), solver=FeasibleSolver()).plan(
        problem, knowledge, problem.runtime, deadline()
    )
    assert result.status == "VALIDATED", result.failure
    assert result.candidate.metrics.makespan_sec == 1860
    assert result.candidate.metrics.cooking_finish_spread_sec == 1200
    assert result.candidate.metrics.max_continuous_human_sec == 360
    assert result.selected_candidate_source == "CP_SAT"
    assert any(t.stage == "TAIL_COMPACTION" for t in result.timings)
    assert ScheduleValidator().validate(knowledge, problem.runtime, problem, result.candidate).valid


def test_compaction_keeps_a_real_rest_when_the_earliest_slot_joins_human_blocks():
    knowledge, problem = cooking_case()
    problem = allow_late_plating(problem)
    original = plated_candidate(problem)
    # 把第二菜人工准备移到540..720；第一菜600秒可收尾，但直接接在720秒
    # 会把180秒连续人工延长到240秒，应保留60秒休息并在780秒开始。
    second_tasks = {
        t.task_id
        for t in problem.logical_tasks
        if t.recipe_instance_id == problem.recipe_instances[1].recipe_instance_id
    }
    original = original.model_copy(
        update={
            "assignments": tuple(
                a.model_copy(
                    update={
                        "interval": Interval(
                            start_sec=a.interval.start_sec + 360,
                            end_sec=a.interval.end_sec + 360,
                        )
                    }
                )
                if a.task_ids[0] in second_tasks and a.interval.start_sec in {180, 360}
                else a
                for a in original.assignments
            )
        }
    )
    assert ScheduleValidator().validate(knowledge, problem.runtime, problem, original).valid
    compacted = compact_cooking_tails(original, problem, deadline())
    first_finish = next(a for a in compacted.assignments if a.interval.start_sec == 780)
    assert first_finish.interval.end_sec == 840
    assert compacted.metrics.makespan_sec == 2220
    assert compacted.metrics.max_continuous_human_sec == 180
    assert (
        compacted.metrics.recipe_cooking_finishes
        == compute_metrics(original, problem).recipe_cooking_finishes
    )
    assert ScheduleValidator().validate(knowledge, problem.runtime, problem, compacted).valid


def test_compaction_respects_a_hard_earliest_start_for_plating():
    knowledge, problem = cooking_case()
    problem = allow_late_plating(problem)
    problem = problem.model_copy(
        update={
            "logical_tasks": tuple(
                t.model_copy(update={"earliest_start_sec": 3000})
                if t.operation_id.root == "finish"
                else t
                for t in problem.logical_tasks
            )
        }
    )
    compacted = compact_cooking_tails(plated_candidate(problem), problem, deadline())
    tasks = {t.task_id: t for t in problem.logical_tasks}
    assert all(
        a.interval.start_sec >= 3000
        for a in compacted.assignments
        if tasks[a.task_ids[0]].operation_id.root == "finish"
    )
    assert ScheduleValidator().validate(knowledge, problem.runtime, problem, compacted).valid


def test_expired_compaction_returns_the_original_complete_schedule():
    import time

    from app.domain.ports import Deadline

    _, problem = cooking_case()
    problem = allow_late_plating(problem)
    original = plated_candidate(problem)
    assert (
        compact_cooking_tails(original, problem, Deadline(expires_at_ns=time.monotonic_ns() - 1))
        is original
    )


def test_legacy_workflow_spread_does_not_move_plating_apart():
    _, problem = cooking_case()
    problem = allow_late_plating(problem)
    objective = problem.policy.objective.model_copy(update={"spread_basis": "WORKFLOW_FINISH"})
    problem = problem.model_copy(
        update={"policy": problem.policy.model_copy(update={"objective": objective})}
    )
    original = plated_candidate(problem)
    assert compact_cooking_tails(original, problem, deadline()) is original
