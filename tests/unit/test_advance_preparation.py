"""显式合成工艺检验开工前备料；原时长及中途冷却保持原知识。"""

import hashlib
import json
import time
from pathlib import Path

import pytest

from app.compiler.compiler import ProblemCompiler
from app.domain.base import content_hash
from app.domain.canonical_recipe import CanonicalRecipeModel
from app.domain.knowledge import MenuKnowledgeView, ReleaseRef
from app.domain.policy import SchedulingPolicy
from app.domain.ports import Deadline
from app.domain.runtime_facts import RuntimeDetails
from app.domain.runtime_session import RuntimeSession
from app.domain.runtime_snapshot import ExecutionRecord
from app.domain.scheduling_problem import SchedulingProblem
from app.runtime.menu_events import apply_menu
from app.scheduling.cp_sat import CpSatScheduler
from app.scheduling.greedy import GreedyScheduler
from app.validation.schedule import ScheduleValidator
from tests.compiler_support import runtime
from tests.runtime_support import event


def preparation_case(*, enabled=True):
    def requirement(key, spec):
        return {
            "requirement_id": key,
            "spec_id": spec,
            "quantity_kind": "EXACT",
            "quantity": {"value": 100, "unit": "g", "scale": 1},
            "provenance_refs": ["synthetic:preparation"],
        }

    stages = [
        ("wash", "WASH", 30, "raw", "washed"),
        ("soak", "WAIT", 14400, "washed", "soaked"),
        ("heat", "HEAT", 60, "soaked", "hot"),
        ("cool", "WAIT", 1200, "hot", "cooled"),
        ("finish", "FINISH", 30, "cooled", "final"),
    ]
    recipe = CanonicalRecipeModel.model_validate(
        {
            "schema_version": "1",
            "recipe_id": "synthetic-advance",
            "name": "合成提前浸泡",
            "recipe_version": "1",
            "provenance_refs": ["synthetic:preparation"],
            "ingredient_requirements": [requirement("raw", "raw")],
            "material_specs": [
                {"spec_id": s, "ingredient_id": "rice", "name": s, "state": s}
                for s in ("raw", "washed", "soaked", "hot", "cooled", "final")
            ],
            "operations": [
                {
                    "operation_id": key,
                    "action": action,
                    "description": key,
                    "duration": {"execution_sec": seconds},
                    "material_inputs": [requirement(key + "-in", before)],
                    "material_outputs": [requirement(key + "-out", after)],
                    "resource_requirements": []
                    if action == "WAIT"
                    else [
                        {
                            "resource_type": "HUMAN",
                            "resource_id": "human_1",
                            "conflict_policy": "UNARY",
                        }
                    ],
                    "provenance_refs": ["synthetic:preparation"],
                }
                for key, action, seconds, before, after in stages
            ],
            "dependencies": [
                {
                    "predecessor_id": a[0],
                    "successor_id": b[0],
                    "reason": "synthetic material flow",
                    "evidence_refs": ["synthetic:preparation"],
                }
                for a, b in zip(stages, stages[1:], strict=False)
            ],
        }
    )
    knowledge = MenuKnowledgeView(
        release=ReleaseRef(
            release_id="synthetic-advance",
            knowledge_version="synthetic-advance",
            rule_version="synthetic-advance",
            snapshot_id="synthetic-advance",
            manifest_hash="a" * 64,
            release_kind="sample",
        ),
        snapshot_schema_version="1",
        snapshot_hash="b" * 64,
        recipes=(recipe,),
        devices=(),
        profiles=(),
        rules=(),
        provenance_index=(),
    )
    policy = SchedulingPolicy(policy_version="synthetic-advance")
    if enabled:
        policy = SchedulingPolicy.model_validate(
            {
                **policy.model_dump(),
                "advance_preparation_mode": "ASSUME_READY",
                "advance_preparation_rules": (
                    {
                        "rule_id": "synthetic:soak",
                        "recipe_id": recipe.recipe_id,
                        "recipe_hash": content_hash(recipe),
                        "operation_ids": ("wash", "soak"),
                        "description": "提前洗净并浸泡",
                        "evidence_refs": ("synthetic:preparation",),
                    },
                ),
            }
        )
    state = runtime(knowledge, details=RuntimeDetails())
    session = RuntimeSession(
        runtime=state,
        policy=SchedulingPolicy(policy_version="synthetic-advance"),
        knowledge_release_id="synthetic-advance",
    ).model_copy(update={"policy": policy})
    return knowledge, session


def menu_session(*, enabled=True, at=0):
    knowledge, session = preparation_case(enabled=enabled)
    recipe = knowledge.recipes[0]
    addition = event(
        session,
        "prepare-menu",
        "START_SESSION",
        {"recipes": [{"id": recipe.recipe_id.root, "name": recipe.name}]},
        at=at,
    )
    session = apply_menu(session, addition, knowledge)
    session = session.model_copy(
        update={"runtime": session.runtime.model_copy(update={"now_offset_sec": at})}
    )
    return knowledge, session


def compile_case(knowledge, session):
    problem = ProblemCompiler().compile(
        knowledge,
        session.menu,
        session.runtime,
        session.policy,
        Deadline(expires_at_ns=time.monotonic_ns() + 5_000_000_000),
    )
    assert isinstance(problem, SchedulingProblem), problem
    return problem


def test_menu_declares_preparation_without_fake_execution():
    knowledge, session = menu_session(at=90)
    declarations = session.runtime.details.model_dump().get("advance_preparations", [])
    assert len(declarations) == 1
    assert declarations[0]["available_at_sec"] == 90
    assert declarations[0]["source_kind"] == "USER_POLICY_ASSUMPTION"
    assert session.runtime.executions == ()
    assert session.runtime.details.occupancies == ()
    prepared = [lot for lot in session.runtime.details.lots if lot.source_spec_id == "soaked"]
    assert len(prepared) == 1 and prepared[0].produced_by_execution_id is None
    assert "USER_POLICY_ASSUMPTION" in prepared[0].availability_evidence
    assert prepared[0].available.numerator == 100
    assert (
        sum(
            lot.available.numerator
            for lot in session.runtime.details.lots
            if lot.source_spec_id == "raw"
        )
        == 0
    )


def test_only_preparation_removed_from_real_schedule_and_source_kept():
    knowledge, session = menu_session()
    problem = compile_case(knowledge, session)
    covered = {task for carrier in problem.standalone_candidates for task in carrier.covers}
    by_op = {task.operation_id.root: task for task in problem.logical_tasks}
    assert by_op["soak"].task_id not in covered
    assert by_op["wash"].task_id not in covered
    assert by_op["cool"].task_id in covered
    assert by_op["soak"].operation.duration.execution_sec == 14400
    assert by_op["heat"].task_id in covered
    result = GreedyScheduler().solve(
        problem, Deadline(expires_at_ns=time.monotonic_ns() + 3_000_000_000)
    )
    assert result.candidate is not None, result
    checked = ScheduleValidator().validate(knowledge, session.runtime, problem, result.candidate)
    assert checked.valid, checked.violations


def test_old_policy_has_no_new_serialized_fields_and_schedules_wait():
    knowledge, session = menu_session(enabled=False)
    problem = compile_case(knowledge, session)
    assert "advance_preparation_mode" not in session.policy.model_dump()
    assert "advance_preparation_rules" not in session.policy.model_dump()
    assert "advance_preparations" not in problem.model_dump()
    covered = {task for carrier in problem.standalone_candidates for task in carrier.covers}
    assert all(task.task_id in covered for task in problem.logical_tasks)


def test_heat_ancestor_cannot_be_declared_prepared():
    knowledge, session = preparation_case()
    payload = session.policy.model_dump()
    payload["advance_preparation_rules"] = [
        {
            "rule_id": "synthetic:unsafe-cooling",
            "recipe_id": "synthetic-advance",
            "recipe_hash": content_hash(knowledge.recipes[0]),
            "operation_ids": ["wash", "soak", "heat", "cool"],
            "description": "不能提前略过蒸煮后的冷却",
            "evidence_refs": ["synthetic:preparation"],
        }
    ]
    changed = SchedulingPolicy.model_validate(payload)
    session = session.model_copy(update={"policy": changed})
    with pytest.raises(ValueError, match="加热|厨具|热加工"):
        apply_menu(
            session,
            event(
                session,
                "unsafe",
                "START_SESSION",
                {"recipes": [{"id": "synthetic-advance", "name": "合成提前浸泡"}]},
            ),
            knowledge,
        )


def test_complete_dish_with_prepared_inputs_cannot_be_cancelled():
    knowledge, session = menu_session()
    problem = compile_case(knowledge, session)
    prepared = {task for item in problem.advance_preparations for task in item.task_ids}
    record = ExecutionRecord(
        execution_id="synthetic-completed",
        task_ids=tuple(
            task.task_id for task in problem.logical_tasks if task.task_id not in prepared
        ),
        status="COMPLETED",
        source="SIMULATED",
        event_refs=("synthetic:complete",),
        started_at=session.runtime.time_origin.at(0),
        finished_at=session.runtime.time_origin.at(1290),
    )
    session = session.model_copy(
        update={
            "runtime": session.runtime.model_copy(
                update={"executions": (record,), "now_offset_sec": 1290}
            )
        }
    )
    with pytest.raises(ValueError, match="已完成"):
        apply_menu(
            session,
            event(
                session,
                "cannot-cancel",
                "CANCEL_RECIPE",
                {"recipe_instance_id": session.menu[0].recipe_instance_id},
                at=1290,
            ),
            knowledge,
        )


def test_prepared_cpsat_preserves_cooling_and_rejects_false_source():
    knowledge, session = menu_session(at=90)
    problem = compile_case(knowledge, session)
    result = CpSatScheduler().solve(
        problem, None, Deadline(expires_at_ns=time.monotonic_ns() + 3_000_000_000)
    )
    assert result.candidate is not None, result
    assert all(assignment.interval.start_sec >= 90 for assignment in result.candidate.assignments)
    assert ScheduleValidator().validate(knowledge, session.runtime, problem, result.candidate).valid
    changed = problem.model_copy(
        update={
            "policy": problem.policy.model_copy(update={"advance_preparation_mode": "SCHEDULE_ALL"})
        }
    )
    candidate = result.candidate.model_copy(update={"problem_hash": changed.problem_hash})
    checked = ScheduleValidator().validate(knowledge, session.runtime, changed, candidate)
    assert not checked.valid and any(v.code == "PREPARATION_SOURCE" for v in checked.violations)


def test_old_archived_problem_identity_is_unchanged():
    root = Path(__file__).resolve().parents[2]
    source = json.loads(
        (root / "data/verification/2026-10-08-reservation-spans/original.problem.json").read_text(
            encoding="utf-8"
        )
    )
    parsed = SchedulingProblem.model_validate(source)
    assert parsed.model_dump(mode="json") == source
    expected = hashlib.sha256(
        json.dumps(
            source, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    ).hexdigest()
    assert parsed.problem_hash == expected
