"""合成反例检验独立校验；数据及编译器都不能自证候选正确。"""

import pytest

from app.domain.schedule import CandidateSchedule, ScheduledAssignment
from app.domain.time import Interval
from app.validation.schedule import ScheduleValidator
from tests.unit.test_compiler_material_stock import source
from tests.unit.test_problem_compilation import compile_menu
from tests.validator_support import resource_example


def example():
    knowledge, state, _, _ = source()
    problem = compile_menu(knowledge.recipes[0], state=state, knowledge=knowledge)
    assignments = []
    for i, task in enumerate(problem.logical_tasks):
        carrier = next(c for c in problem.standalone_candidates if c.covers == (task.task_id,))
        assignments.append(
            ScheduledAssignment(
                carrier_id=carrier.carrier_id,
                task_ids=carrier.covers,
                interval=Interval(start_sec=i * 60, end_sec=(i + 1) * 60),
                resource_uses=carrier.resource_uses,
            )
        )
    candidate = CandidateSchedule(problem_hash=problem.problem_hash, assignments=tuple(assignments))
    return knowledge, state, problem, candidate


def validate(knowledge, state, problem, candidate):
    return ScheduleValidator().validate(knowledge, state, problem, candidate)


def test_touching_intervals_are_legal_and_validation_is_hash_bound():
    knowledge, state, problem, candidate = example()
    report = validate(knowledge, state, problem, candidate)
    assert report.valid, report.violations
    assert report.candidate_hash == candidate.candidate_hash
    assert report.problem_hash == problem.problem_hash


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("missing", "COVERAGE"),
        ("duplicate", "COVERAGE"),
        ("overlap", "RESOURCE_OVERLAP"),
        ("duration", "DURATION"),
        ("foreign_human", "RESOURCE_IDENTITY"),
        ("material", "MATERIAL_BINDING"),
        ("source_removed", "SOURCE_COVERAGE"),
        ("source_operation", "SOURCE_OPERATION"),
        ("dependency_removed", "SOURCE_DEPENDENCY"),
        ("stale", "IDENTITY"),
    ],
)
def test_mutated_plans_or_problem_cannot_pass(mutation, code):
    knowledge, state, problem, candidate = example()
    a, b = candidate.assignments
    if mutation == "missing":
        candidate = candidate.model_copy(update={"assignments": (a,)})
    elif mutation == "duplicate":
        candidate = candidate.model_copy(update={"assignments": (a, a, b)})
    elif mutation == "overlap":
        candidate = candidate.model_copy(
            update={"assignments": (a, b.model_copy(update={"interval": a.interval}))}
        )
    elif mutation == "duration":
        candidate = candidate.model_copy(
            update={
                "assignments": (
                    a,
                    b.model_copy(update={"interval": Interval(start_sec=60, end_sec=180)}),
                )
            }
        )
    elif mutation == "foreign_human":
        human = b.resource_uses[0].model_copy(update={"resource_id": "human_2"})
        candidate = candidate.model_copy(
            update={"assignments": (a, b.model_copy(update={"resource_uses": (human,)}))}
        )
    elif mutation == "material":
        problem = problem.model_copy(
            update={"material_flow": problem.material_flow.model_copy(update={"demands": ()})}
        )
    elif mutation == "source_removed":
        problem = problem.model_copy(
            update={"logical_tasks": problem.logical_tasks[:1], "dependencies": ()}
        )
        candidate = candidate.model_copy(update={"assignments": (a,)})
    elif mutation == "dependency_removed":
        problem = problem.model_copy(update={"dependencies": ()})
    elif mutation == "source_operation":
        first, *other = problem.logical_tasks
        changed = first.operation.model_copy(update={"description": "未审核的操作说明"})
        problem = problem.model_copy(
            update={"logical_tasks": (first.model_copy(update={"operation": changed}), *other)}
        )
    else:
        state = state.model_copy(update={"state_revision": 1})
    candidate = candidate.model_copy(update={"problem_hash": problem.problem_hash})
    report = validate(knowledge, state, problem, candidate)
    assert not report.valid
    assert code in {v.code for v in report.violations}, report.violations


def test_validation_rechecks_contents_after_caller_cached_problem_hash():
    knowledge, state, problem, candidate = example()
    original_hash = problem.problem_hash
    changed = problem.model_copy(update={"dependencies": ()})
    assert original_hash != changed.problem_hash
    report = validate(knowledge, state, changed, candidate)
    assert not report.valid
    assert report.problem_hash == changed.problem_hash
    assert "IDENTITY" in {violation.code for violation in report.violations}


@pytest.mark.parametrize(
    "policy,independent,human,different,expected",
    [
        ("UNARY", False, False, False, False),
        ("UNARY", True, False, False, True),
        ("UNARY", True, True, False, False),
        ("BATCH_EXCLUSIVE", False, False, False, False),
        ("STATE_COMPATIBLE", False, False, False, True),
        ("STATE_COMPATIBLE", False, False, True, False),
        ("SHARED_AUXILIARY", False, False, False, True),
        ("SHARED_AUXILIARY", False, False, True, False),
    ],
)
def test_physical_device_matrix(policy, independent, human, different, expected):
    data = resource_example(policy, independent=independent, human=human, different=different)
    report = validate(*data)
    assert report.valid is expected, report.violations


def test_completed_fact_cannot_be_rewritten_or_rescheduled():
    from tests.unit.test_compiler_material_stock import completed_case

    knowledge, state, _, _ = completed_case()
    problem = compile_menu(knowledge.recipes[0], state=state, knowledge=knowledge)
    carrier = problem.standalone_candidates[0]
    candidate = CandidateSchedule(
        problem_hash=problem.problem_hash,
        assignments=(
            ScheduledAssignment(
                carrier_id=carrier.carrier_id,
                task_ids=carrier.covers,
                interval=Interval(start_sec=60, end_sec=120),
                resource_uses=carrier.resource_uses,
            ),
        ),
    )
    assert validate(knowledge, state, problem, candidate).valid
    execution = problem.fixed_executions[0].model_copy(
        update={"finished_at": state.time_origin.at(30)}
    )
    altered = problem.model_copy(update={"fixed_executions": (execution,)})
    candidate = candidate.model_copy(update={"problem_hash": altered.problem_hash})
    assert "HISTORY" in {v.code for v in validate(knowledge, state, altered, candidate).violations}


def test_candidate_cannot_claim_different_material_ports():
    knowledge, state, problem, candidate = example()
    carriers = tuple(
        c.model_copy(update={"material_outputs": ()}) for c in problem.standalone_candidates
    )
    problem = problem.model_copy(update={"standalone_candidates": carriers})
    candidate = candidate.model_copy(update={"problem_hash": problem.problem_hash})
    assert "MATERIAL_BINDING" in {
        v.code for v in validate(knowledge, state, problem, candidate).violations
    }
