"""可选串行菜谱策略的正式依赖与独立反篡改检查。"""

import time

import pytest

from app.compiler.compiler import ProblemCompiler
from app.domain.base import content_hash
from app.domain.canonical_recipe import CanonicalRecipeModel
from app.domain.policy import SchedulingPolicy
from app.domain.ports import Deadline
from app.domain.schedule import CandidateSchedule, ScheduledAssignment, ValidatedSchedule
from app.domain.scheduling_problem import SchedulingProblem
from app.domain.time import Interval
from app.runtime.service import RuntimeService
from app.scheduling.greedy import GreedyScheduler
from app.scheduling.metrics import compute_metrics
from app.validation.schedule import ScheduleValidator
from benchmarks.robustness.observed_session import bind_plan, event, start_session
from benchmarks.robustness.simulator import TrajectorySimulator
from tests.runtime_support import ORIGIN, policy, synthetic_knowledge


def instance(serial):
    knowledge = synthetic_knowledge()
    selected = SchedulingPolicy.model_validate(
        {
            **policy().model_dump(mode="json"),
            **({"critical_window_policy_id": "SERIAL_RECIPE_V1"} if serial else {}),
        }
    )
    session = start_session(
        knowledge,
        selected,
        {"case_id": "critical-synthetic", "recipe_ids": ["synthetic-0", "synthetic-1"]},
        ORIGIN,
    )
    deadline = Deadline(expires_at_ns=time.monotonic_ns() + 4_200_000_000)
    problem = ProblemCompiler().compile(
        knowledge, session.menu, session.runtime, selected, deadline
    )
    assert isinstance(problem, SchedulingProblem), problem
    candidate = GreedyScheduler().solve(problem, deadline).candidate
    assert candidate is not None
    candidate = candidate.model_copy(update={"metrics": compute_metrics(candidate, problem)})
    return knowledge, problem, candidate


def test_optional_policy_serializes_complete_recipes_and_validates():
    knowledge, problem, candidate = instance(True)
    proof = ScheduleValidator().validate(knowledge, problem.runtime, problem, candidate)
    assert proof.valid, proof
    intervals = {t: a.interval for a in candidate.assignments for t in a.task_ids}
    groups = [
        [
            intervals[t.task_id]
            for t in problem.logical_tasks
            if t.recipe_instance_id == recipe.recipe_instance_id
        ]
        for recipe in problem.recipe_instances
    ]
    assert max(s.end_sec for s in groups[0]) <= min(s.start_sec for s in groups[1])
    assert any(d.evidence_refs == ("policy:SERIAL_RECIPE_V1",) for d in problem.dependencies)


def test_deleted_policy_dependencies_are_rejected_by_independent_validator():
    knowledge, problem, candidate = instance(True)
    forged = problem.model_copy(
        update={
            "dependencies": tuple(
                d for d in problem.dependencies if d.evidence_refs != ("policy:SERIAL_RECIPE_V1",)
            )
        }
    )
    rebound = candidate.model_copy(update={"problem_hash": forged.problem_hash})
    proof = ScheduleValidator().validate(knowledge, forged.runtime, forged, rebound)
    assert not proof.valid
    assert any(v.code == "SOURCE_DEPENDENCY" for v in proof.violations)


def test_serial_policy_rejects_an_otherwise_legal_parallel_schedule():
    knowledge, parallel, candidate = instance(False)
    _, serial, _ = instance(True)
    forged = candidate.model_copy(update={"problem_hash": serial.problem_hash})
    proof = ScheduleValidator().validate(knowledge, serial.runtime, serial, forged)
    assert not proof.valid
    assert any(v.code == "PRECEDENCE" for v in proof.violations)
    assert parallel.policy != serial.policy


def test_default_policy_preserves_existing_json_identity():
    selected = policy()
    assert "critical_window_policy_id" not in selected.model_dump(mode="json")


def test_actual_runtime_rejects_starting_second_recipe_before_first_finishes():
    knowledge, problem, candidate = instance(True)
    session = start_session(
        knowledge,
        problem.policy,
        {"case_id": "critical-synthetic", "recipe_ids": ["synthetic-0", "synthetic-1"]},
        ORIGIN,
    )
    proof = ScheduleValidator().validate(knowledge, problem.runtime, problem, candidate)
    session = bind_plan(session, problem, ValidatedSchedule(candidate=candidate, validation=proof))
    target = next(
        t
        for t in problem.logical_tasks
        if t.recipe_instance_id == problem.recipe_instances[1].recipe_instance_id
        and t.operation_id.root == "mix"
    )
    request = event(
        session,
        "reject-other-recipe",
        "OPERATION_STARTED",
        {"task_id": target.task_id, "execution_id": "premature-second"},
        0,
    )
    before = content_hash(session)
    # 调用实际状态转换路径；本次非法事件在任何 SQL 写入前即拒绝。
    runtime = RuntimeService(None, knowledge, None)
    with pytest.raises(ValueError, match="真实前置"):
        runtime._transition(session, request)
    assert content_hash(session) == before


def test_serial_policy_rescues_known_zero_gap_conflict_without_duration_inflation():
    source = synthetic_knowledge()
    recipes = [r.model_dump(mode="json") for r in source.recipes]
    for op, seconds in zip(recipes[0]["operations"], (10, 60, 10), strict=True):
        op["duration"]["execution_sec"] = seconds
    recipes[0]["dependencies"][1]["max_lag_sec"] = 0
    recipes[1]["operations"] = recipes[1]["operations"][:1]
    recipes[1]["dependencies"] = []
    knowledge = source.model_copy(
        update={"recipes": tuple(CanonicalRecipeModel.model_validate(r) for r in recipes)}
    )

    class SlowerOtherRecipe:
        def duration(self, nominal, operations):
            return (240 if nominal == 180 else nominal), nominal != 60

    results = []
    for serial in (False, True):
        selected = policy().model_copy(
            update={"critical_window_policy_id": "SERIAL_RECIPE_V1" if serial else "NONE"}
        )
        session = start_session(
            knowledge,
            selected,
            {"case_id": "critical-rescue", "recipe_ids": ["synthetic-0", "synthetic-1"]},
            ORIGIN,
        )
        deadline = Deadline(expires_at_ns=time.monotonic_ns() + 4_200_000_000)
        problem = ProblemCompiler().compile(
            knowledge, session.menu, session.runtime, selected, deadline
        )
        if serial:
            candidate = GreedyScheduler().solve(problem, deadline).candidate
        else:
            times = {
                (0, "mix"): (0, 10),
                (0, "wait"): (130, 190),
                (0, "finish"): (190, 200),
                (1, "mix"): (10, 190),
            }
            indices = {i.recipe_instance_id: n for n, i in enumerate(session.menu)}
            tasks = {t.task_id: t for t in problem.logical_tasks}
            assignments = []
            for carrier in problem.standalone_candidates:
                task = tasks[carrier.covers[0]]
                lo, hi = times[(indices[task.recipe_instance_id], task.operation_id.root)]
                assignments.append(
                    ScheduledAssignment(
                        carrier_id=carrier.carrier_id,
                        task_ids=carrier.covers,
                        interval=Interval(start_sec=lo, end_sec=hi),
                        resource_uses=carrier.resource_uses,
                    )
                )
            candidate = CandidateSchedule(
                problem_hash=problem.problem_hash, assignments=tuple(assignments)
            )
        candidate = candidate.model_copy(update={"metrics": compute_metrics(candidate, problem)})
        proof = ScheduleValidator().validate(knowledge, problem.runtime, problem, candidate)
        assert proof.valid, proof
        session = bind_plan(
            session, problem, ValidatedSchedule(candidate=candidate, validation=proof)
        )
        results.append(
            TrajectorySimulator(
                knowledge,
                session,
                problem,
                candidate,
                SlowerOtherRecipe(),
                {"max_events": 100, "trigger_delay_sec": 30},
            ).run()
        )
    assert results[0]["status"] == "FAILED"
    assert results[0]["failure_detail"]["code"] == "ACTUAL_WINDOW_EXPIRED"
    assert results[1]["status"] == "COMPLETED", results[1]["failure"]
    assert results[1]["validation"]["valid"]
    assert results[1]["protected_stage_count"] == 1
