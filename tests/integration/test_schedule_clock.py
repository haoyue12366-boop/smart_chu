"""合成工艺 + 独立 SQLite：时钟推断有来源，立即重排冻结在途事实。"""

from datetime import timedelta

import pytest

from app.domain.canonical_recipe import CanonicalRecipeModel
from app.domain.events import EventSource
from app.domain.knowledge import MenuKnowledgeView, ReleaseRef
from app.domain.policy import ObjectiveSpec, SchedulingPolicy
from app.runtime.clock import SimulationClock
from app.runtime.service import RuntimeService
from app.scheduling.cp_sat import CpSatScheduler
from app.services.planning import PlanningService
from app.storage.unit_of_work import UnitOfWork
from tests.runtime_support import ORIGIN, event


def clock_knowledge(*, parallel=False):
    def amount(identity, spec):
        return {
            "requirement_id": identity,
            "spec_id": spec,
            "quantity_kind": "EXACT",
            "quantity": {"value": 10, "scale": 1, "unit": "g"},
            "provenance_refs": ["synthetic:clock"],
        }

    recipes = []
    for index in range(2):
        stages = [("prep", 120, True), ("wait", 240, False), ("finish", 60, True)]
        if parallel:
            stages = [("wait", 120 + index * 60, False)]
        specs = ("raw", "prepared", "rested", "ready")
        operations = []
        for n, (name, duration, human) in enumerate(stages):
            operations.append(
                {
                    "operation_id": name,
                    "action": "MIX" if human else "WAIT",
                    "duration": {"execution_sec": duration},
                    "resource_requirements": [
                        {
                            "resource_type": "HUMAN",
                            "resource_id": "human_1",
                            "conflict_policy": "UNARY",
                        }
                    ]
                    if human
                    else [],
                    "material_inputs": [amount(name + "-in", specs[n])],
                    "material_outputs": [amount(name + "-out", specs[n + 1])],
                }
            )
        recipes.append(
            CanonicalRecipeModel.model_validate(
                {
                    "schema_version": "1.0",
                    "recipe_id": f"clock-{index}",
                    "recipe_version": "1",
                    "name": f"合成时钟工艺{index}",
                    "provenance_refs": ["synthetic:clock"],
                    "ingredient_requirements": [amount("ingredient", "raw")],
                    "material_specs": [
                        {"spec_id": s, "ingredient_id": "synthetic", "name": s, "state": s}
                        for s in specs[: len(stages) + 1]
                    ],
                    "operations": operations,
                    "dependencies": [
                        {
                            "predecessor_id": before[0],
                            "successor_id": after[0],
                            "reason": "合成物料先后",
                            "evidence_refs": ["synthetic:clock"],
                        }
                        for before, after in zip(stages, stages[1:], strict=False)
                    ],
                }
            )
        )
    return MenuKnowledgeView(
        release=ReleaseRef(
            release_id="synthetic-clock",
            knowledge_version="synthetic-clock-v1",
            rule_version="synthetic-clock-v1",
            snapshot_id="synthetic-clock",
            manifest_hash="0" * 64,
            release_kind="sample",
        ),
        snapshot_schema_version="1.0",
        snapshot_hash="0" * 64,
        recipes=tuple(recipes),
        devices=(),
        profiles=(),
        rules=(),
        provenance_index=(),
    )


def clock_service(tmp_path, *, mode="SCHEDULE_CLOCK", parallel=False, solver=None, knowledge=None):
    knowledge = knowledge or clock_knowledge(parallel=parallel)
    store = UnitOfWork(tmp_path / "clock.sqlite")
    store.migrate()
    wall_clock = SimulationClock(ORIGIN)
    runtime = RuntimeService(store, knowledge, wall_clock)
    session = runtime.create_session(
        "clock",
        mode,
        ORIGIN,
        SchedulingPolicy(
            policy_version="synthetic-clock", objective=ObjectiveSpec(stages=("MAKESPAN",))
        ),
    )
    planner = PlanningService(runtime, solver or CpSatScheduler())
    recipes = knowledge.recipes if parallel else knowledge.recipes[:1]
    first = planner.apply_event(
        event(
            session,
            "start",
            "START_SESSION",
            {
                "recipes": [{"id": r.recipe_id, "name": r.name} for r in recipes],
            },
        )
    )
    assert first.status == "PUBLISHED", first
    return runtime, planner, first


def tick(runtime, at):
    from app.runtime.schedule_clock import ClockExecutionService

    runtime.clock.advance(at)
    return ClockExecutionService(runtime).advance("clock")


def request_replan(runtime, planner, at, identity="request"):
    current = runtime.get("clock")
    return planner.apply_event(event(current, identity, "REPLAN_REQUESTED", {}, at=at))


def test_clock_publishes_immediately_and_keeps_running_prefix(tmp_path):
    runtime, planner, _ = clock_service(tmp_path)
    current = tick(runtime, 60)
    assert len(current.runtime.executions) == 1
    running = current.runtime.executions[0]
    assert running.status == "RUNNING"
    assert running.source == "SCHEDULE_CLOCK"
    assert not running.completed_task_ids

    replanned = request_replan(runtime, planner, 60)
    assert replanned.status == "PUBLISHED", replanned
    assert replanned.plan.plan_version == 2
    assert runtime.get("clock").runtime.executions == (running,)
    assert not runtime.get("clock").dispatch_blocked

    tick(runtime, 119)
    assert planner.drain("clock").status == "NO_REPLAN"
    assert runtime.get("clock").runtime.executions[0].status == "RUNNING"
    tick(runtime, 120)
    before = runtime.get("clock")
    finished = before.runtime.executions[0]
    assert finished.status == "COMPLETED"
    assert len(before.runtime.executions) == 2
    assert finished.task_spans[0].interval.start_sec == 0
    assert finished.task_spans[0].interval.end_sec == 120

    after = runtime.get("clock")
    assert after.runtime.executions[0] == finished
    assert all(
        set(a.task_ids).isdisjoint(finished.task_ids)
        for a in replanned.plan.validated.candidate.assignments
    )
    assert after.schedule_clock.started_at == before.schedule_clock.started_at
    assert after.schedule_clock.replan_not_before_sec is None
    runtime.store.close()


def test_repeated_ticks_and_restart_do_not_duplicate_material_effects(tmp_path):
    runtime, _, _ = clock_service(tmp_path)
    final = tick(runtime, 420)
    assert len(final.runtime.executions) == 3
    assert all(e.status == "COMPLETED" for e in final.runtime.executions)
    assert {e.source for e in final.runtime.executions} == {EventSource.SCHEDULE_CLOCK}
    assert len([entry for entry in final.ledger if entry.kind == "CONSUME"]) == 3
    assert len([entry for entry in final.ledger if entry.kind == "PRODUCE"]) == 3
    final_lot = next(lot for lot in final.runtime.details.lots if lot.source_spec_id == "ready")
    assert final_lot.available.fraction() == 10
    again = tick(runtime, 420)
    assert again == final

    knowledge = runtime.knowledge
    runtime.store.close()
    store = UnitOfWork(tmp_path / "clock.sqlite")
    store.migrate()
    restarted_clock = SimulationClock(ORIGIN)
    restarted_clock.advance(500)
    recovered = RuntimeService(store, knowledge, restarted_clock)
    from app.runtime.schedule_clock import ClockExecutionService

    after = ClockExecutionService(recovered).advance("clock")
    assert after.runtime.executions == final.runtime.executions
    assert after.ledger == final.ledger
    assert after.schedule_clock.started_at == final.schedule_clock.started_at
    assert after.schedule_clock.processed_until_sec == 500
    store.close()


def test_parallel_running_steps_publish_without_waiting_for_latest_finish(tmp_path):
    runtime, planner, _ = clock_service(tmp_path, parallel=True)
    current = tick(runtime, 60)
    assert len([e for e in current.runtime.executions if e.status == "RUNNING"]) == 2
    result = request_replan(runtime, planner, 60)
    assert result.status == "PUBLISHED", result
    assert runtime.get("clock").runtime.executions == current.runtime.executions
    tick(runtime, 120)
    assert planner.drain("clock").status == "NO_REPLAN"
    assert sorted(e.status for e in runtime.get("clock").runtime.executions) == [
        "COMPLETED",
        "RUNNING",
    ]
    tick(runtime, 180)
    assert planner.drain("clock").status == "NO_REPLAN"
    runtime.store.close()


@pytest.mark.parametrize("mode", ["MANUAL_CONFIRM", "DEVICE_FEEDBACK"])
def test_clock_does_not_infer_manual_or_device_execution(tmp_path, mode):
    runtime, _, _ = clock_service(tmp_path, mode=mode)
    before = runtime.get("clock")
    after = tick(runtime, 500)
    assert after == before
    assert not after.runtime.executions
    assert after.schedule_clock is None
    runtime.store.close()


def test_manual_remaining_duration_correction_immediately_replans_future(tmp_path):
    runtime, planner, _ = clock_service(tmp_path)
    tick(runtime, 60)
    assert request_replan(runtime, planner, 60).status == "PUBLISHED"
    current = tick(runtime, 70)
    record = current.runtime.executions[0]
    correction = event(
        current,
        "correction",
        "DURATION_UPDATED",
        {
            "task_id": record.task_ids[0],
            "execution_id": record.execution_id,
            "remaining_sec": 100,
        },
        at=70,
    ).model_copy(update={"source": EventSource.MANUAL_CONFIRM})
    result = planner.apply_event(correction)
    assert result.status == "PUBLISHED", result
    assert result.event.status == "APPLIED"
    assert runtime.get("clock").runtime.executions[0].task_spans[0].interval.end_sec == 170
    tick(runtime, 120)
    assert runtime.get("clock").runtime.executions[0].status == "RUNNING"
    tick(runtime, 170)
    assert planner.drain("clock").status == "NO_REPLAN"
    runtime.store.close()


def test_clock_starts_at_successful_publication_and_rejects_future_inference(tmp_path):
    class DelayedSolver:
        clock = None

        def solve(self, *args, **kwargs):
            self.clock.advance(20)
            return CpSatScheduler().solve(*args, **kwargs)

    # 求解器只注入测试墙钟延迟，编译、求解、校验和发布仍走真实路径。
    source = clock_knowledge()
    store = UnitOfWork(tmp_path / "clock.sqlite")
    store.migrate()
    wall = SimulationClock(ORIGIN)
    runtime = RuntimeService(store, source, wall)
    current = runtime.create_session(
        "clock", "SCHEDULE_CLOCK", ORIGIN, SchedulingPolicy(policy_version="clock")
    )
    solver = DelayedSolver()
    solver.clock = wall
    result = PlanningService(runtime, solver).apply_event(
        event(
            current,
            "start",
            "START_SESSION",
            {
                "recipes": [{"id": source.recipes[0].recipe_id, "name": source.recipes[0].name}],
            },
        )
    )
    assert result.status == "PUBLISHED", result
    current = runtime.get("clock")
    assert current.schedule_clock.started_at == ORIGIN + timedelta(seconds=20)
    from app.domain.runtime_clock import clock_offset
    from app.runtime.schedule_clock import ClockExecutionService

    assert clock_offset(current, ORIGIN + timedelta(seconds=25)) == 5
    wall.advance(25)
    with pytest.raises(ValueError, match="未来|当前"):
        ClockExecutionService(runtime).advance("clock", until_sec=26)
    assert ClockExecutionService(runtime).advance("clock").schedule_clock.processed_until_sec == 5
    store.close()
