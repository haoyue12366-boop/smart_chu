"""统一总耗时上限、独立候选池及后续阶段失败回退。"""

import pytest

from app.domain.objectives import ObjectiveStage
from app.domain.reports import SolveResult
from app.scheduling.candidate_pool import CandidatePool
from app.scheduling.cp_sat import CpSatScheduler
from app.scheduling.engine import PlanningEngine
from app.scheduling.objectives import makespan_cap
from app.validation.schedule import ScheduleValidator
from tests.unit.test_cp_sat_model import deadline
from tests.unit.test_schedule_validator import example
from tests.validator_support import resource_example


@pytest.mark.parametrize(
    "seconds,grid,serial,expected",
    [
        (100, 1, None, 105),
        (101, 1, None, 107),
        (100, 60, None, 160),
        (3600, 1, None, 3720),
        (3600, 1, 3650, 3650),
    ],
)
def test_total_time_cap_uses_integer_arithmetic(seconds, grid, serial, expected):
    _, _, problem, _ = example()
    policy = problem.policy.model_copy(update={"time_grid_sec": grid})
    assert makespan_cap(seconds, policy, serial) == expected


def test_candidate_pool_rejects_invalid_or_wrong_state_candidate():
    knowledge, state, problem, candidate = example()
    pool = CandidatePool(problem, knowledge, state, ScheduleValidator())
    bad = candidate.model_copy(update={"assignments": candidate.assignments[:1]})
    assert not pool.add(bad)
    assert pool.best() is None
    assert pool.add(candidate)
    assert pool.best().validation.valid
    assert pool.best().candidate.metrics.makespan_sec == 120


def test_exact_human_stage_matches_independent_metric():
    knowledge, state, problem, _ = resource_example(independent=True, human=True)
    result = CpSatScheduler().solve(
        problem, None, deadline(), stage=ObjectiveStage(name="D_HUMAN", makespan_cap_sec=240)
    )
    assert result.status == "OPTIMAL", result
    pool = CandidatePool(problem, knowledge, state, ScheduleValidator())
    assert pool.add(result.candidate)
    assert result.objective_value == pool.best().candidate.metrics.max_continuous_human_sec
    assert result.objective_value == 120


def test_later_solver_unknown_retains_validated_greedy_candidate():
    class UnknownSolver:
        def solve(self, problem, hint, deadline, **kwargs):
            return SolveResult(status="UNKNOWN", problem_hash=problem.problem_hash)

    knowledge, state, problem, _ = resource_example()
    engine = PlanningEngine(validator=ScheduleValidator(), solver=UnknownSolver())
    result = engine.plan(problem, knowledge, state, deadline())
    assert result.status == "VALIDATED", result
    assert result.validation.valid
    assert not result.human_objective_optimized
    assert result.candidate.metrics.makespan_sec == 180


def test_budget_or_state_failure_does_not_return_old_plan():
    knowledge, state, problem, _ = example()
    engine = PlanningEngine(validator=ScheduleValidator())
    assert engine.plan(problem, knowledge, state, deadline(0)).status == "FAILED"
    newer = state.model_copy(update={"state_revision": 1})
    result = engine.plan(problem, knowledge, newer, deadline())
    assert result.status == "FAILED"
    assert result.failure.failure_class == "STALE_STATE"


def test_rejected_human_stage_does_not_claim_optimization():
    knowledge, state, problem, invalid = resource_example()

    class InvalidHumanSolver:
        def solve(self, problem, hint, deadline, **kwargs):
            stage = kwargs.get("stage")
            if stage is not None and stage.name == "D_HUMAN":
                return SolveResult(
                    status="FEASIBLE", problem_hash=problem.problem_hash, candidate=invalid
                )
            return SolveResult(status="UNKNOWN", problem_hash=problem.problem_hash)

    result = PlanningEngine(validator=ScheduleValidator(), solver=InvalidHumanSolver()).plan(
        problem, knowledge, state, deadline()
    )
    assert result.status == "VALIDATED"
    assert not result.human_objective_optimized
    assert result.rejected_candidates


def test_stability_stage_does_not_penalize_unchanged_task_times():
    knowledge, state, problem, old = example()
    result = CpSatScheduler().solve(
        problem,
        None,
        deadline(),
        stage=ObjectiveStage(name="D_STABILITY", makespan_cap_sec=180, previous_plan=old),
    )
    assert result.status == "OPTIMAL", result
    assert result.objective_value == 0
    assert ScheduleValidator().validate(knowledge, state, problem, result.candidate).valid


def test_engine_runs_stability_only_with_explicit_previous_plan():
    knowledge, state, problem, previous = example()
    engine = PlanningEngine(validator=ScheduleValidator(), previous_plan=previous)
    result = engine.plan(problem, knowledge, state, deadline())
    assert result.status == "VALIDATED", result
    assert result.stability_objective_optimized
    assert any(r.objective_stage == "D_STABILITY" for r in result.stage_results)
    assert result.candidate.metrics.makespan_sec <= result.makespan_cap_sec


def test_human_stage_threshold_is_reported_without_false_optimization():
    knowledge, state, problem, _ = resource_example(independent=True, human=True)
    problem = problem.model_copy(
        update={"policy": problem.policy.model_copy(update={"max_exact_human_phases": 1})}
    )
    result = PlanningEngine(validator=ScheduleValidator()).plan(
        problem, knowledge, state, deadline()
    )
    assert result.status == "VALIDATED", result
    assert not result.human_objective_optimized
    assert not any(r.objective_stage == "D_HUMAN" for r in result.stage_results)


def test_parallel_plan_cannot_be_mislabeled_as_serial_reference():
    from app.domain.schedule import CandidateSchedule, ScheduledAssignment
    from app.domain.time import Interval
    from tests.unit.test_problem_compilation import compile_menu

    knowledge, state, _, _ = resource_example("STATE_COMPATIBLE")
    problem = compile_menu(
        knowledge.recipes[0], knowledge.recipes[0], state=state, knowledge=knowledge
    )
    parallel = CandidateSchedule(
        problem_hash=problem.problem_hash,
        assignments=tuple(
            ScheduledAssignment(
                carrier_id=c.carrier_id,
                task_ids=c.covers,
                interval=Interval(start_sec=0, end_sec=c.duration_sec),
                resource_uses=c.resource_uses,
            )
            for c in problem.standalone_candidates
        ),
    )

    class ParallelSolver:
        def solve(self, problem, hint, deadline, **kwargs):
            return SolveResult(
                status="FEASIBLE", problem_hash=problem.problem_hash, candidate=parallel
            )

    result = PlanningEngine(validator=ScheduleValidator(), solver=ParallelSolver()).plan(
        problem, knowledge, state, deadline()
    )
    assert result.status == "VALIDATED"
    assert result.serial_reference_candidate is None
    assert result.serial_reference_validation is None
