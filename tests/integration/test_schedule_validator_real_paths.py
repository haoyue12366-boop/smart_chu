"""旧开发工具仅提供测试候选；新 Validator 独立核验真实发布路径。"""

import json
from functools import lru_cache

import pytest

from app.domain.schedule import CandidateSchedule, ScheduledAssignment
from app.domain.time import Interval
from app.validation.schedule import ScheduleValidator
from scripts.check_scheduling_dataset import solve
from tests.compiler_support import ROOT, published_knowledge, runtime
from tests.unit.test_problem_compilation import compile_menu


@lru_cache(maxsize=1)
def source_rows():
    return json.loads(
        (ROOT / "data/development/development-v3-rebased-v2/dataset.json").read_text(
            encoding="utf-8"
        )
    )["recipes"]


def real_candidate(name):
    knowledge = published_knowledge()
    recipe = (
        knowledge.recipes[name]
        if isinstance(name, int)
        else next(r for r in knowledge.recipes if r.name == name)
    )
    row = next(r for r in source_rows() if r["recipe_id"] == recipe.recipe_id.root)
    legacy = solve([row])
    assert legacy["tasks"], legacy
    state = runtime(knowledge)
    problem = compile_menu(recipe, knowledge=knowledge, state=state)
    intervals = {
        t["operation_id"]: Interval(start_sec=t["start_sec"], end_sec=t["end_sec"])
        for t in legacy["tasks"]
    }
    assignments = []
    for task in problem.logical_tasks:
        selected = {
            legacy["resource_assignments"][r["reservation_id"]]
            for r in row["resource_reservations"]
            if task.operation_id.root in r["members"]
        }
        carrier = next(
            c
            for c in problem.standalone_candidates
            if c.covers == (task.task_id,) and selected <= {u.resource_id for u in c.resource_uses}
        )
        assignments.append(
            ScheduledAssignment(
                carrier_id=carrier.carrier_id,
                task_ids=carrier.covers,
                interval=intervals[task.operation_id.root],
                resource_uses=carrier.resource_uses,
            )
        )
    return (
        knowledge,
        state,
        problem,
        CandidateSchedule(problem_hash=problem.problem_hash, assignments=assignments),
    )


@pytest.mark.parametrize("name", ["亲朋欢聚套餐", "烹香酷炒汇", "韩式泡菜鸦片鱼头"])
def test_published_complex_path_is_independently_valid(name):
    data = real_candidate(name)
    report = ScheduleValidator().validate(*data)
    assert report.valid, report.violations


def test_real_fixed_program_cannot_wait_at_intervention():
    knowledge, state, problem, candidate = real_candidate("亲朋欢聚套餐")
    intervention = problem.mandatory_programs.programs[0].intervention_members[0]
    altered = []
    for assignment in candidate.assignments:
        if intervention in assignment.task_ids:
            assignment = assignment.model_copy(
                update={
                    "interval": Interval(
                        start_sec=assignment.interval.start_sec + 1,
                        end_sec=assignment.interval.end_sec + 1,
                    )
                }
            )
        altered.append(assignment)
    candidate = candidate.model_copy(update={"assignments": tuple(altered)})
    report = ScheduleValidator().validate(knowledge, state, problem, candidate)
    assert "FIXED_PROGRAM" in {v.code for v in report.violations}


@pytest.mark.parametrize("index", range(100))
def test_all_published_single_paths_compile_and_validate(index):
    data = real_candidate(index)
    report = ScheduleValidator().validate(*data)
    assert report.valid, (data[2].recipe_instances[0].name, report.violations)
