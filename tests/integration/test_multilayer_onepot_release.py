"""真实100菜发布的双层修订、旧快照兼容、准备规则及单次工序通知。"""

import time

from app.compiler.compiler import ProblemCompiler
from app.config import ROOT, AppSettings
from app.domain.base import content_hash
from app.domain.policy import SchedulingPolicy
from app.domain.ports import Deadline
from app.domain.runtime_facts import RuntimeDetails
from app.domain.runtime_session import RuntimeSession
from app.domain.schedule import PublishedPlan, ValidatedSchedule
from app.domain.scheduling_problem import SchedulingProblem
from app.knowledge.loader import load_release, read_release_ref
from app.knowledge.repository import SnapshotLease
from app.runtime.menu_events import apply_menu
from app.runtime.notification_templates import operation_notice
from app.scheduling.greedy import GreedyScheduler
from app.validation.schedule import ScheduleValidator
from tests.compiler_support import runtime
from tests.runtime_support import event


def test_real_onepot_full_reservation_occupies_two_layers_without_duplicate_work():
    settings = AppSettings()
    reference = read_release_ref(settings.release_root, settings.release_id)
    loaded = load_release(settings.release_root, reference)
    baseline = load_release(
        settings.release_root,
        read_release_ref(settings.release_root, "delegated-v3-layered-devices-v1-all"),
    )
    assert baseline.snapshot.content_hash == (
        "c66756cf0e4713cb0cb07a220fb746efd7a1b581fa6a40d585763db21e76d5a4"
    )
    before = {r.recipe_id: r for r in baseline.snapshot.knowledge.recipes}
    after = {r.recipe_id: r for r in loaded.snapshot.knowledge.recipes}
    changed = [rid for rid in before if before[rid] != after[rid]]
    assert len(before) == len(after) == 100
    assert [rid.root for rid in changed] == ["66715324bfbee338853895c7"]
    recipe_id = changed[0]
    recipe = after[recipe_id]
    expected = {"op_013_01", "op_014_01", "op_014_02", "op_014_03", "op_1000_01"}
    for old, new in zip(before[recipe_id].operations, recipe.operations, strict=True):
        assert new.model_copy(update={"resource_requirements": old.resource_requirements}) == old
        assert tuple(u for u in new.resource_requirements if u.resource_type == "HUMAN") == tuple(
            u for u in old.resource_requirements if u.resource_type == "HUMAN"
        )
        uses = [u for u in new.resource_requirements if u.resource_id == "steam_oven_1"]
        if new.operation_id.root in expected:
            assert len(uses) == 1
            assert uses[0].occupied_layer_indices == (1, 3)
            assert uses[0].units == 2
        else:
            assert not uses
    context = next(
        c for c in loaded.snapshot.knowledge.scope.recipe_contexts if c.recipe_id == recipe_id
    )
    reservation = next(
        r for r in context.resource_reservations if r.resource_options == ("steam_oven_1",)
    )
    assert {member.root for member in reservation.members} == expected
    original_policy = SchedulingPolicy.model_validate_json(
        (ROOT / "data/policies/p6-cook-prepared-v1.json").read_bytes()
    )
    policy = SchedulingPolicy.model_validate_json(settings.policy_path.read_bytes())
    assert len(policy.advance_preparation_rules) == 42
    assert sum(len(r.operation_ids) for r in policy.advance_preparation_rules) == 148
    for old, new in zip(
        original_policy.advance_preparation_rules, policy.advance_preparation_rules, strict=True
    ):
        assert new.model_copy(update={"recipe_hash": old.recipe_hash}) == old
        assert new.recipe_hash == content_hash(after[new.recipe_id])
    knowledge = SnapshotLease(reference, loaded).select((recipe_id,))
    seed = RuntimeSession(
        runtime=runtime(knowledge, details=RuntimeDetails()),
        policy=policy,
        knowledge_release_id=reference.release_id,
    )
    session = apply_menu(
        seed,
        event(
            seed,
            "real-onepot",
            "START_SESSION",
            {"recipes": [{"id": recipe_id.root, "name": recipe.name}]},
        ),
        knowledge,
    )
    problem = ProblemCompiler().compile(
        knowledge,
        session.menu,
        session.runtime,
        policy,
        Deadline(expires_at_ns=time.monotonic_ns() + 5_000_000_000),
    )
    assert isinstance(problem, SchedulingProblem), problem
    assert len(problem.advance_preparations) == 1
    assert {o.root for o in problem.advance_preparations[0].operation_ids} == {
        "op_001_01",
        "op_002_01",
        "op_003_01",
    }
    result = GreedyScheduler().solve(
        problem, Deadline(expires_at_ns=time.monotonic_ns() + 3_000_000_000)
    )
    assert result.candidate is not None, result
    checked = ScheduleValidator().validate(knowledge, session.runtime, problem, result.candidate)
    assert checked.valid, checked.violations
    tasks = {t.task_id: t for t in problem.logical_tasks}
    steam_assignments = [
        a
        for a in result.candidate.assignments
        if any(u.resource_id == "steam_oven_1" for u in a.resource_uses)
    ]
    assert len(steam_assignments) == 5
    assert {
        tasks[tid].operation_id.root for a in steam_assignments for tid in a.task_ids
    } == expected
    for assignment in steam_assignments:
        use = next(u for u in assignment.resource_uses if u.resource_id == "steam_oven_1")
        assert use.occupied_layer_indices == (1, 3) and use.units == 2
    assert sum(a.interval.end_sec - a.interval.start_sec for a in steam_assignments) == 2370
    heat = next(t for t in problem.logical_tasks if t.operation_id.root == "op_014_03")
    plan = PublishedPlan(
        session_id=session.runtime.session_id,
        plan_version=1,
        parent_plan_version=0,
        state_revision=session.runtime.state_revision,
        knowledge_version=reference.knowledge_version,
        snapshot_id=reference.snapshot_id,
        time_origin=session.runtime.time_origin,
        validated=ValidatedSchedule(candidate=result.candidate, validation=checked),
        publication_id="test-real-multilayer-onepot",
        committed_at=session.runtime.time_origin.start_at,
    )
    notice = operation_notice(problem, (heat.task_id,), "START", plan)
    assert "第1、3层" in notice
    assert notice.count(heat.operation.description) == 1
