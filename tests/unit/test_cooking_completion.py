"""合成反例：出锅与装盘分开；新的目标契约必须可编译和独立校验。"""

import pytest

from app.compiler.compiler import ProblemCompiler
from app.domain.policy import ObjectiveSpec
from app.domain.recipe_context import RecipeSchedulingContext
from app.domain.reports import CompilationFailure
from app.domain.scheduling_problem import SchedulingProblem
from tests.compiler_support import menu_for, runtime
from tests.runtime_support import policy, synthetic_knowledge
from tests.unit.test_cp_sat_model import deadline


def cooking_case():
    knowledge = synthetic_knowledge()
    recipes = tuple(
        recipe.model_copy(
            update={
                "operations": tuple(
                    op.model_copy(
                        update={
                            "duration": op.duration.model_copy(
                                update={"execution_sec": 420 if i == 0 else 1440}
                            )
                        }
                    )
                    if op.operation_id.root == "wait"
                    else op
                    for op in recipe.operations
                )
            }
        )
        for i, recipe in enumerate(knowledge.recipes)
    )
    knowledge = knowledge.model_copy(update={"recipes": recipes})
    contexts = tuple(
        RecipeSchedulingContext.model_validate(
            {
                "recipe_id": recipe.recipe_id.root,
                "cooking_completion": {
                    "operation_ids": ["wait"],
                    "kind": "NO_HEAT_READY",
                    "evidence_refs": ["synthetic"],
                },
            }
        )
        for recipe in knowledge.recipes
    )
    knowledge = knowledge.model_copy(update={"recipe_contexts": contexts})
    objective = ObjectiveSpec.model_validate(
        {
            "stages": ["SPREAD", "HUMAN_BUSY", "MAKESPAN"],
            "spread_basis": "COOKING_FINISH",
            "spread_target_sec": 300,
        }
    )
    selected_policy = policy().model_copy(update={"objective": objective})
    problem = ProblemCompiler().compile(
        knowledge, menu_for(*knowledge.recipes), runtime(knowledge), selected_policy, deadline()
    )
    assert isinstance(problem, SchedulingProblem), problem
    return knowledge, problem


def test_compiler_binds_cooking_boundaries_to_each_recipe_instance():
    _, problem = cooking_case()
    assert len(problem.cooking_completions) == 2
    for boundary in problem.cooking_completions:
        tasks = [task for task in problem.logical_tasks if task.task_id in boundary.task_ids]
        assert len(tasks) == 1
        assert tasks[0].operation_id.root == "wait"
        assert tasks[0].recipe_instance_id == boundary.recipe_instance_id


def test_new_objective_rejects_missing_cooking_boundary_instead_of_using_plating():
    knowledge, problem = cooking_case()
    missing = knowledge.model_copy(update={"recipe_contexts": ()})
    result = ProblemCompiler().compile(
        missing, problem.recipe_instances, problem.runtime, problem.policy, deadline()
    )
    assert isinstance(result, CompilationFailure)
    assert "出锅" in str(result)


def test_invalid_operation_boundary_is_rejected():
    knowledge, problem = cooking_case()
    rule = knowledge.recipe_contexts[0].cooking_completion
    from app.domain.ids import OperationId

    bad_context = knowledge.recipe_contexts[0].model_copy(
        update={
            "cooking_completion": rule.model_copy(
                update={"operation_ids": (OperationId("missing"),)}
            )
        }
    )
    changed = knowledge.model_copy(
        update={"recipe_contexts": (bad_context, *knowledge.recipe_contexts[1:])}
    )
    result = ProblemCompiler().compile(
        changed, problem.recipe_instances, problem.runtime, problem.policy, deadline()
    )
    assert isinstance(result, CompilationFailure)


def test_legacy_contracts_do_not_add_fields_to_hashed_json():
    assert "cooking_completion" not in RecipeSchedulingContext(recipe_id="legacy").model_dump()
    assert "spread_basis" not in ObjectiveSpec().model_dump()


@pytest.mark.parametrize("ids", [[], ["wait", "wait"]])
def test_cooking_boundary_requires_distinct_nonempty_operations(ids):
    from app.domain.cooking_completion import CookingCompletionRule

    with pytest.raises(ValueError):
        CookingCompletionRule(operation_ids=ids, kind="NO_HEAT_READY", evidence_refs=["synthetic"])


def plated_candidate(problem):
    from app.domain.schedule import CandidateSchedule, ScheduledAssignment
    from app.domain.time import Interval

    periods = (
        {"mix": (0, 180), "wait": (180, 600), "finish": (3540, 3600)},
        {"mix": (180, 360), "wait": (360, 1800), "finish": (3600, 3660)},
    )
    tasks = {task.task_id: task for task in problem.logical_tasks}
    instances = {
        instance.recipe_instance_id: i for i, instance in enumerate(problem.recipe_instances)
    }
    assignments = []
    for carrier in problem.standalone_candidates:
        task = tasks[carrier.covers[0]]
        start, end = periods[instances[task.recipe_instance_id]][task.operation_id.root]
        assignments.append(
            ScheduledAssignment(
                carrier_id=carrier.carrier_id,
                task_ids=carrier.covers,
                interval=Interval(start_sec=start, end_sec=end),
                resource_uses=carrier.resource_uses,
            )
        )
    return CandidateSchedule(problem_hash=problem.problem_hash, assignments=tuple(assignments))


def allow_late_plating(problem):
    # 合成反例显式扩大时域，确保延后装盘仍是完整合法候选。
    return problem.model_copy(
        update={
            "horizon_sec": 7200,
            "logical_tasks": tuple(
                t.model_copy(update={"latest_end_sec": 7200}) for t in problem.logical_tasks
            ),
        }
    )


def test_plating_alignment_does_not_hide_twenty_minute_cooking_spread():
    from app.scheduling.metrics import compute_metrics
    from app.validation.schedule import ScheduleValidator

    knowledge, problem = cooking_case()
    problem = allow_late_plating(problem)
    candidate = plated_candidate(problem)
    metrics = compute_metrics(candidate, problem)
    assert metrics.cooking_finish_spread_sec == 1200
    assert metrics.completion_spread_sec == 60
    assert metrics.makespan_sec == 3660
    assert [finish.cooking_finish_sec for finish in metrics.recipe_cooking_finishes] == [600, 1800]
    report = ScheduleValidator().validate(
        knowledge, problem.runtime, problem, candidate.model_copy(update={"metrics": metrics})
    )
    assert report.valid, report.violations
    forged = metrics.model_copy(update={"cooking_finish_spread_sec": 60})
    report = ScheduleValidator().validate(
        knowledge, problem.runtime, problem, candidate.model_copy(update={"metrics": forged})
    )
    assert "METRICS" in {v.code for v in report.violations}


def test_validator_rejects_replacing_published_cooking_anchor_with_plating():
    from app.domain.cooking_completion import RecipeCookingCompletion
    from app.validation.schedule import ScheduleValidator

    knowledge, problem = cooking_case()
    forged = tuple(
        RecipeCookingCompletion(
            recipe_instance_id=b.recipe_instance_id,
            task_ids=tuple(
                t.task_id
                for t in problem.logical_tasks
                if t.recipe_instance_id == b.recipe_instance_id and t.operation_id.root == "finish"
            ),
            kind=b.kind,
            evidence_refs=b.evidence_refs,
        )
        for b in problem.cooking_completions
    )
    changed = problem.model_copy(update={"cooking_completions": forged})
    report = ScheduleValidator().validate(
        knowledge, changed.runtime, changed, plated_candidate(changed)
    )
    assert "COOKING_COMPLETION_SOURCE" in {v.code for v in report.violations}


def test_solver_targets_cooking_instead_of_delaying_plating():
    from app.domain.objectives import ObjectiveStage
    from app.scheduling.cp_sat import CpSatScheduler
    from app.scheduling.metrics import compute_metrics

    _, problem = cooking_case()
    result = CpSatScheduler().solve(
        problem, None, deadline(), stage=ObjectiveStage(name="B_SPREAD", spread_excess_cap_sec=0)
    )
    assert result.candidate is not None, result
    assert compute_metrics(result.candidate, problem).cooking_finish_spread_sec <= 300


def test_default_continuous_objective_prefers_breaks_over_less_total_work():
    from app.domain.objectives import ObjectiveStage
    from app.scheduling.cp_sat import CpSatScheduler
    from app.scheduling.metrics import compute_metrics
    from tests.unit.test_total_human_objective import witness

    problem = witness()
    objective = problem.policy.objective.model_copy(
        update={"stages": ("SPREAD", "HUMAN_BUSY", "MAKESPAN"), "spread_target_sec": 300}
    )
    problem = problem.model_copy(
        update={"policy": problem.policy.model_copy(update={"objective": objective})}
    )
    assert problem.policy.quality_first
    result = CpSatScheduler().solve(
        problem,
        None,
        deadline(),
        stage=ObjectiveStage(name="E_QUALITY", makespan_cap_sec=360, spread_excess_cap_sec=0),
    )
    assert result.candidate is not None, result
    metrics = compute_metrics(result.candidate, problem)
    assert metrics.max_continuous_human_sec == 120
    assert metrics.total_human_work_sec == 240  # 180秒的共批连续更长，不该因总人工更少而胜出。


def test_candidate_ranking_uses_cooking_then_continuous_work_then_makespan():
    from app.domain.schedule import CandidateSchedule, ScheduleMetrics
    from app.scheduling.ranking import candidate_rank

    _, problem = cooking_case()

    def rank(cooking, human, total, end):
        metric = ScheduleMetrics(
            makespan_sec=end,
            completion_spread_sec=0,
            cooking_finish_spread_sec=cooking,
            max_continuous_human_sec=human,
            total_human_work_sec=total,
        )
        return candidate_rank(
            CandidateSchedule(problem_hash=problem.problem_hash, assignments=(), metrics=metric),
            problem,
        )

    assert rank(200, 60, 500, 1000) < rank(200, 120, 100, 900)
    assert rank(200, 60, 500, 1000) < rank(200, 60, 100, 1100)
    assert rank(200, 120, 500, 1000) < rank(900, 60, 100, 900)


def test_continuous_search_timeout_keeps_a_cooking_aligned_candidate():
    from app.domain.reports import SolveResult
    from app.scheduling.cp_sat import CpSatScheduler
    from app.scheduling.engine import PlanningEngine
    from app.validation.schedule import ScheduleValidator

    knowledge, problem = cooking_case()
    seen = []

    class SlowHumanSolver:
        def solve(self, problem, hint, deadline, **kwargs):
            stage = kwargs.get("stage")
            seen.append(stage.name if stage else None)
            if stage and stage.name == "E_QUALITY":
                return SolveResult(status="UNKNOWN", problem_hash=problem.problem_hash)
            return CpSatScheduler().solve(problem, hint, deadline, **kwargs)

    result = PlanningEngine(validator=ScheduleValidator(), solver=SlowHumanSolver()).plan(
        problem, knowledge, problem.runtime, deadline()
    )
    assert result.status == "VALIDATED", result
    assert "B_SPREAD" in seen
    assert seen.index("B_SPREAD") < seen.index("E_QUALITY")
    assert result.candidate.metrics.cooking_finish_spread_sec <= 300
    assert not result.human_objective_optimized


def test_lightweight_seed_does_not_build_unused_quadratic_human_hint(monkeypatch):
    from app.domain.objectives import ObjectiveStage
    from app.scheduling import human_hint
    from app.scheduling.cp_sat import CpSatScheduler

    _, problem = cooking_case()
    problem = allow_late_plating(problem)

    def forbidden(*args):
        raise RuntimeError("轻量阶段不能生成用不到的人工链提示")

    monkeypatch.setattr(human_hint, "add_human_chain_hint", forbidden)
    result = CpSatScheduler().solve(
        problem, plated_candidate(problem), deadline(), stage=ObjectiveStage(name="C_MAKESPAN")
    )
    assert result.candidate is not None, result


def test_human_hint_respects_shared_deadline():
    from app.domain.objectives import ObjectiveStage
    from app.scheduling.human_hint import add_human_chain_hint
    from app.scheduling.model_builder import ModelBuilder
    from app.scheduling.objectives import apply_stage

    _, problem = cooking_case()
    builder = ModelBuilder(problem, deadline())
    builder.build()
    apply_stage(builder, ObjectiveStage(name="D_HUMAN"))
    builder.deadline = deadline(0)
    with pytest.raises(TimeoutError):
        add_human_chain_hint(builder, plated_candidate(problem))


@pytest.mark.parametrize(
    "operation,evidence", [("missing", "synthetic-source"), ("heat", "unknown-evidence")]
)
def test_knowledge_gate_checks_cooking_operation_and_evidence(operation, evidence):
    from app.domain.cooking_completion import CookingCompletionRule
    from app.validation.knowledge import validate_knowledge
    from tests.unit.test_knowledge_gate import fixture, scope

    recipe, profiles, device = fixture()
    context = RecipeSchedulingContext(
        recipe_id=recipe.recipe_id,
        cooking_completion=CookingCompletionRule(
            operation_ids=[operation], kind="OUT_OF_POT", evidence_refs=[evidence]
        ),
    )
    release_scope = scope(device).model_copy(update={"recipe_contexts": (context,)})
    report = validate_knowledge((recipe,), profiles, (), release_scope)
    assert not report.valid
    assert {v.code for v in report.violations} & {"COOKING_COMPLETION", "MISSING_EVIDENCE"}
