"""显式合成反例：改善出锅差必须移动完整设备预约，不能延后取出充数。"""

import json
from pathlib import Path

import pytest

from app.compiler.compiler import ProblemCompiler
from app.domain.canonical_recipe import CanonicalRecipeModel
from app.domain.knowledge import MenuKnowledgeView
from app.domain.policy import SchedulingPolicy
from app.domain.recipe_context import RecipeSchedulingContext
from app.domain.reports import SolveResult
from app.domain.schedule import CandidateSchedule, ScheduledAssignment
from app.domain.scheduling_problem import SchedulingProblem
from app.domain.time import Interval
from app.scheduling.engine import PlanningEngine
from app.validation.schedule import ScheduleValidator
from tests.compiler_support import menu_for, runtime
from tests.unit.test_cp_sat_model import deadline


def cooking_reservation_case(*, tight=False):
    human = {"resource_type": "HUMAN", "resource_id": "human_1", "conflict_policy": "UNARY"}
    device = {
        "resource_type": "DEVICE",
        "resource_id": "synthetic-oven",
        "physical_resource_id": "synthetic-oven",
        "component_id": "cavity",
        "conflict_policy": "UNARY",
        "rule_version": "synthetic-rule",
        "evidence_refs": ["synthetic"],
        "configuration": [
            {"parameter": "mode", "value": "bake"},
            {"parameter": "temperature_c", "value": 100},
        ],
    }
    operation_sets = (
        (
            ("prep", 60, [human]),
            ("load", 60, [human, device]),
            ("heat", 600, [device]),
            ("unload", 60, [human, device]),
            ("plate", 60, [human]),
        ),
        (("mix", 180, [human]), ("wait", 1800, []), ("finish", 60, [human])),
    )
    recipes = tuple(
        CanonicalRecipeModel(
            schema_version="1.0",
            recipe_id=f"synthetic-{i}",
            recipe_version="1",
            name=f"合成出锅{i}",
            provenance_refs=("synthetic",),
            operations=[
                {
                    "operation_id": name,
                    "action": "HEAT" if name == "heat" else "MIX",
                    "duration": {"execution_sec": duration},
                    "resource_requirements": uses,
                }
                for name, duration, uses in operations
            ],
            ingredient_requirements=(),
            material_specs=(),
            dependencies=[
                {
                    "predecessor_id": left[0],
                    "successor_id": right[0],
                    "max_lag_sec": 0
                    if tight and left[0] == "load" and right[0] == "heat"
                    else None,
                    "reason": "合成顺序",
                    "evidence_refs": ["synthetic"],
                }
                for left, right in zip(operations, operations[1:], strict=False)
            ],
        )
        for i, operations in enumerate(operation_sets)
    )
    contexts = tuple(
        RecipeSchedulingContext.model_validate(
            {
                "recipe_id": recipe.recipe_id,
                "cooking_completion": {
                    "operation_ids": ["unload" if i == 0 else "wait"],
                    "kind": "OUT_OF_POT" if i == 0 else "NO_HEAT_READY",
                    "evidence_refs": ["synthetic"],
                },
                "resource_reservations": [
                    {
                        "reservation_id": "whole-oven",
                        "members": ["load", "heat", "unload"],
                        "resource_options": ["synthetic-oven"],
                        "policy": "UNARY",
                        "span": "min_start_to_max_end",
                        "origin": "SOURCE_EXPLICIT",
                    }
                ]
                if i == 0
                else [],
            }
        )
        for i, recipe in enumerate(recipes)
    )
    knowledge = MenuKnowledgeView.model_validate(
        {
            "release": {
                "release_id": "synthetic",
                "knowledge_version": "synthetic",
                "rule_version": "synthetic-rule",
                "snapshot_id": "synthetic",
                "manifest_hash": "0" * 64,
                "release_kind": "development",
            },
            "snapshot_schema_version": "1.0",
            "snapshot_hash": "0" * 64,
            "recipes": recipes,
            "recipe_contexts": contexts,
            "rules": [],
            "devices": [
                {
                    "device_instance_id": "synthetic-oven",
                    "physical_resource_id": "synthetic-oven",
                    "component_id": "cavity",
                    "capability_refs": ["synthetic-profile"],
                    "conflict_policy": "UNARY",
                    "rule_version": "synthetic-rule",
                    "evidence_refs": ["synthetic"],
                }
            ],
            "profiles": [
                {
                    "profile_id": "synthetic-profile",
                    "device_type": "oven",
                    "mode": "bake",
                    "constraints": [{"parameter": "temperature_c", "minimum": 0, "maximum": 250}],
                    "rule_version": "synthetic-rule",
                    "provenance_refs": ["synthetic"],
                }
            ],
            "provenance_index": [
                {
                    "evidence_id": "synthetic",
                    "artifact_path": "synthetic",
                    "artifact_hash": "0" * 64,
                    "locator": "synthetic",
                }
            ],
        }
    )
    policy = SchedulingPolicy.model_validate(
        json.loads(
            (
                Path(__file__).resolve().parents[2] / "data/policies/p6-cook-layered-v1.json"
            ).read_text(encoding="utf-8")
        )
    )
    problem = ProblemCompiler().compile(
        knowledge, menu_for(*recipes), runtime(knowledge), policy, deadline()
    )
    assert isinstance(problem, SchedulingProblem), problem
    periods = {
        "prep": (0, 60),
        "load": (60, 120),
        "heat": (120, 720),
        "unload": (720, 780),
        "plate": (780, 840),
        "mix": (120, 300),
        "wait": (300, 2100),
        "finish": (2100, 2160),
    }
    tasks = {task.task_id: task for task in problem.logical_tasks}
    candidate = CandidateSchedule(
        problem_hash=problem.problem_hash,
        assignments=tuple(
            ScheduledAssignment(
                carrier_id=carrier.carrier_id,
                task_ids=carrier.covers,
                interval=Interval(
                    start_sec=periods[tasks[carrier.covers[0]].operation_id.root][0],
                    end_sec=periods[tasks[carrier.covers[0]].operation_id.root][1],
                ),
                resource_uses=carrier.resource_uses,
            )
            for carrier in problem.standalone_candidates
        ),
    )
    report = ScheduleValidator().validate(knowledge, problem.runtime, problem, candidate)
    assert report.valid, report.violations
    return knowledge, problem, candidate


def test_engine_aligns_complete_cooking_reservation_before_quality_search():
    knowledge, problem, original = cooking_reservation_case()
    quality_hints = []

    class SeedSolver:
        def solve(self, problem, hint, deadline, **kwargs):
            stage = kwargs["stage"]
            if stage.name == "E_QUALITY":
                quality_hints.append(hint)
                return SolveResult(status="UNKNOWN", problem_hash=problem.problem_hash)
            return SolveResult(
                status="FEASIBLE",
                problem_hash=problem.problem_hash,
                candidate=original,
                objective_stage=stage.name,
            )

    result = PlanningEngine(validator=ScheduleValidator(), solver=SeedSolver()).plan(
        problem, knowledge, problem.runtime, deadline()
    )
    assert result.status == "VALIDATED", result.failure
    assert result.candidate.metrics.cooking_finish_spread_sec == 60
    assert result.candidate.metrics.makespan_sec == 2160
    tasks = {task.task_id: task for task in problem.logical_tasks}
    spans = {
        tasks[a.task_ids[0]].operation_id.root: a.interval for a in result.candidate.assignments
    }
    assert spans["prep"] == Interval(start_sec=0, end_sec=60)
    assert spans["load"] == Interval(start_sec=1320, end_sec=1380)
    assert spans["heat"] == Interval(start_sec=1380, end_sec=1980)
    assert spans["unload"] == Interval(start_sec=1980, end_sec=2040)
    assert spans["plate"] == Interval(start_sec=2040, end_sec=2100)
    assert quality_hints and all(
        hint.metrics.cooking_finish_spread_sec == 60 for hint in quality_hints
    )
    for hint in quality_hints:
        assert ScheduleValidator().validate(knowledge, problem.runtime, problem, hint).valid
    assert ScheduleValidator().validate(knowledge, problem.runtime, problem, result.candidate).valid


def test_reverse_hint_can_move_tied_early_dishes_through_a_spread_plateau():
    from app.domain.ids import RecipeId
    from app.scheduling.metrics import compute_metrics
    from app.scheduling.reverse_hints import align_cooking_finishes

    knowledge, initial, _ = cooking_reservation_case()
    recipes = tuple(
        knowledge.recipes[1].model_copy(
            update={
                "recipe_id": RecipeId(f"plateau-{i}"),
                "operations": tuple(
                    op.model_copy(
                        update={
                            "resource_requirements": (),
                            "duration": op.duration.model_copy(
                                update={"execution_sec": 600 if i < 2 else 1800}
                            )
                            if op.operation_id.root == "wait"
                            else op.duration,
                        }
                    )
                    for op in knowledge.recipes[1].operations
                ),
            }
        )
        for i in range(3)
    )
    knowledge = knowledge.model_copy(
        update={
            "recipes": recipes,
            "recipe_contexts": tuple(
                knowledge.recipe_contexts[1].model_copy(update={"recipe_id": r.recipe_id})
                for r in recipes
            ),
        }
    )
    problem = ProblemCompiler().compile(
        knowledge, menu_for(*recipes), runtime(knowledge), initial.policy, deadline()
    )
    tasks = {task.task_id: task for task in problem.logical_tasks}
    original = CandidateSchedule(
        problem_hash=problem.problem_hash,
        assignments=tuple(
            ScheduledAssignment(
                carrier_id=c.carrier_id,
                task_ids=c.covers,
                resource_uses=(),
                interval=Interval.model_validate(
                    dict(
                        zip(
                            ("start_sec", "end_sec"),
                            {
                                "mix": (0, 180),
                                "wait": (
                                    180,
                                    1980
                                    if tasks[c.covers[0]].recipe_instance_id.root == "instance-2"
                                    else 780,
                                ),
                                "finish": (1980, 2040)
                                if tasks[c.covers[0]].recipe_instance_id.root == "instance-2"
                                else (780, 840),
                            }[tasks[c.covers[0]].operation_id.root],
                            strict=True,
                        )
                    )
                ),
            )
            for c in problem.standalone_candidates
        ),
    )
    assert ScheduleValidator().validate(knowledge, problem.runtime, problem, original).valid
    aligned = align_cooking_finishes(original, problem, deadline())
    assert compute_metrics(aligned, problem).cooking_finish_spread_sec == 0
    assert compute_metrics(aligned, problem).makespan_sec == 2040
    assert ScheduleValidator().validate(knowledge, problem.runtime, problem, aligned).valid


def test_unattainable_spread_keeps_search_inside_the_complete_seed_makespan_bound():
    knowledge, problem, original = cooking_reservation_case()
    problem = problem.model_copy(
        update={
            "logical_tasks": tuple(
                task.model_copy(update={"latest_end_sec": 780})
                if task.operation_id.root == "unload"
                else task
                for task in problem.logical_tasks
            )
        }
    )
    original = original.model_copy(update={"problem_hash": problem.problem_hash})
    stages = []

    class SeedSolver:
        def solve(self, problem, hint, deadline, **kwargs):
            stage = kwargs["stage"]
            stages.append(stage)
            if stage.name == "E_QUALITY":
                return SolveResult(status="UNKNOWN", problem_hash=problem.problem_hash)
            return SolveResult(
                status="FEASIBLE",
                problem_hash=problem.problem_hash,
                candidate=original,
                objective_stage=stage.name,
            )

    result = PlanningEngine(validator=ScheduleValidator(), solver=SeedSolver()).plan(
        problem, knowledge, problem.runtime, deadline()
    )
    assert result.status == "VALIDATED", result.failure
    # 2160 + 5% = 2268；失败的300秒目标不能把质量搜索的界恢复到2880秒串行流程。
    assert all(stage.makespan_cap_sec <= 2268 for stage in stages if stage.name == "E_QUALITY")
    assert any(stage.name == "B_SPREAD" and stage.makespan_cap_sec <= 2268 for stage in stages)
    assert result.candidate.metrics.makespan_sec == 2160


def test_reverse_hint_respects_a_resource_block_for_the_entire_reservation():
    from app.domain.runtime_constraints import ResourceBlock
    from app.scheduling.reverse_hints import align_cooking_finishes

    knowledge, problem, original = cooking_reservation_case()
    block = ResourceBlock(
        resource_id="synthetic-oven",
        physical_resource_id="synthetic-oven",
        component_id="cavity",
        interval=Interval(start_sec=1380, end_sec=1980),
        reason="合成未释放预约",
        evidence_refs=("synthetic",),
    )
    problem = problem.model_copy(update={"resource_blocks": (block,)})
    original = original.model_copy(update={"problem_hash": problem.problem_hash})
    aligned = align_cooking_finishes(original, problem, deadline())
    tasks = {t.task_id: t for t in problem.logical_tasks}
    spans = {tasks[a.task_ids[0]].operation_id.root: a.interval for a in aligned.assignments}
    assert spans["load"] == Interval(start_sec=660, end_sec=720)
    assert spans["heat"] == Interval(start_sec=720, end_sec=1320)
    assert spans["unload"] == Interval(start_sec=1320, end_sec=1380)
    assert aligned.metrics.makespan_sec == 2160
    assert ScheduleValidator().validate(knowledge, problem.runtime, problem, aligned).valid


def test_reverse_hint_preserves_completed_members_of_a_live_reservation():
    from app.domain.runtime_snapshot import ExecutionRecord
    from app.scheduling.reverse_hints import align_cooking_finishes

    knowledge, initial, original = cooking_reservation_case()
    facts = tuple(
        ExecutionRecord(
            execution_id=f"synthetic-execution-{i}",
            task_ids=a.task_ids,
            status="COMPLETED" if a.interval.end_sec <= 720 else "RUNNING",
            source="SIMULATED",
            event_refs=(f"synthetic-event-{i}",),
            started_at=initial.runtime.time_origin.at(a.interval.start_sec),
            finished_at=initial.runtime.time_origin.at(a.interval.end_sec)
            if a.interval.end_sec <= 720
            else None,
            remaining_sec=a.interval.end_sec - 720 if a.interval.end_sec > 720 else None,
            remaining_source_ref="synthetic-observation" if a.interval.end_sec > 720 else None,
            resource_ids=tuple(u.resource_id for u in a.resource_uses),
        )
        for i, a in enumerate(original.assignments)
        if a.interval.start_sec < 720
    )
    state = initial.runtime.model_copy(update={"now_offset_sec": 720, "executions": facts})
    problem = ProblemCompiler().compile(
        knowledge, initial.recipe_instances, state, initial.policy, deadline()
    )
    assert isinstance(problem, SchedulingProblem), problem
    fixed = {task for fact in facts for task in fact.task_ids}
    remaining = original.model_copy(
        update={
            "problem_hash": problem.problem_hash,
            "assignments": tuple(a for a in original.assignments if not set(a.task_ids) & fixed),
        }
    )
    assert ScheduleValidator().validate(knowledge, state, problem, remaining).valid
    before = problem.model_dump_json()
    aligned = align_cooking_finishes(remaining, problem, deadline())
    assert aligned is remaining
    assert problem.model_dump_json() == before
    assert ScheduleValidator().validate(knowledge, state, problem, aligned).valid


def test_expired_reverse_hint_returns_the_original_complete_candidate():
    from app.scheduling.reverse_hints import align_cooking_finishes

    _, problem, original = cooking_reservation_case()
    assert align_cooking_finishes(original, problem, deadline(0)) is original


def test_legacy_workflow_objective_does_not_realign_cooking_anchors():
    from app.scheduling.reverse_hints import align_cooking_finishes

    _, problem, original = cooking_reservation_case()
    problem = problem.model_copy(
        update={
            "policy": problem.policy.model_copy(
                update={
                    "objective": problem.policy.objective.model_copy(
                        update={"spread_basis": "WORKFLOW_FINISH"}
                    )
                }
            )
        }
    )
    original = original.model_copy(update={"problem_hash": problem.problem_hash})
    assert align_cooking_finishes(original, problem, deadline()) is original


@pytest.mark.parametrize("tight", [False, True])
def test_engine_packs_heating_toward_unload_instead_of_publishing_a_long_hot_hold(tight):
    knowledge, problem, original = cooking_reservation_case(tight=tight)
    tasks = {task.task_id: task for task in problem.logical_tasks}
    original = original.model_copy(
        update={
            "assignments": tuple(
                a.model_copy(
                    update={
                        "interval": Interval(
                            start_sec=a.interval.start_sec + 960, end_sec=a.interval.end_sec + 960
                        )
                    }
                )
                if tasks[a.task_ids[0]].operation_id.root in {"unload", "plate"}
                else a
                for a in original.assignments
            )
        }
    )
    assert ScheduleValidator().validate(knowledge, problem.runtime, problem, original).valid

    class SeedSolver:
        def solve(self, problem, hint, deadline, **kwargs):
            stage = kwargs["stage"]
            if stage.name == "E_QUALITY":
                return SolveResult(status="UNKNOWN", problem_hash=problem.problem_hash)
            return SolveResult(
                status="FEASIBLE",
                problem_hash=problem.problem_hash,
                candidate=original,
                objective_stage=stage.name,
            )

    result = PlanningEngine(validator=ScheduleValidator(), solver=SeedSolver()).plan(
        problem, knowledge, problem.runtime, deadline()
    )
    assert result.status == "VALIDATED", result.failure
    spans = {
        tasks[a.task_ids[0]].operation_id.root: a.interval for a in result.candidate.assignments
    }
    assert spans["unload"].start_sec - spans["heat"].end_sec == 0
    assert spans["heat"].end_sec - spans["heat"].start_sec == 600
    assert result.candidate.metrics.cooking_finish_spread_sec == 60
    assert result.candidate.metrics.makespan_sec == 2160
    assert ScheduleValidator().validate(knowledge, problem.runtime, problem, result.candidate).valid


def test_real_cp_sat_meets_attainable_window_with_whole_heating_block():
    from app.scheduling.cp_sat import CpSatScheduler

    knowledge, problem, _ = cooking_reservation_case(tight=True)
    result = PlanningEngine(validator=ScheduleValidator(), solver=CpSatScheduler()).plan(
        problem, knowledge, problem.runtime, deadline()
    )
    assert result.status == "VALIDATED", result.failure
    assert result.candidate.metrics.cooking_finish_spread_sec <= 300
    assert result.candidate.metrics.makespan_sec <= 2160
    assert ScheduleValidator().validate(knowledge, problem.runtime, problem, result.candidate).valid
    tasks = {task.task_id: task for task in problem.logical_tasks}
    spans = {
        tasks[a.task_ids[0]].operation_id.root: a.interval for a in result.candidate.assignments
    }
    assert spans["heat"].end_sec - spans["heat"].start_sec == 600
    assert spans["load"].end_sec == spans["heat"].start_sec
    assert spans["heat"].end_sec == spans["unload"].start_sec
