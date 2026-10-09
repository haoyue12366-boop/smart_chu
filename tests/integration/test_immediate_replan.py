"""即时重排保留运行事实，新增计划在发布生效后使用空闲资源。"""

from app.scheduling.cp_sat import CpSatScheduler
from app.storage.repositories import RuntimeRepository
from tests.integration.test_schedule_clock import (
    clock_knowledge,
    clock_service,
    request_replan,
    tick,
)
from tests.integration.test_schedule_clock_safety import layered_runtime
from tests.runtime_support import event


def test_replan_publishes_while_original_execution_is_still_running(tmp_path):
    runtime, planner, first = clock_service(tmp_path)
    before = tick(runtime, 60)
    result = request_replan(runtime, planner, 60)
    assert result.status == "PUBLISHED", result
    assert result.plan.plan_version == 2
    after = runtime.get("clock")
    assert after.runtime.executions == before.runtime.executions
    assert after.runtime.executions[0].status == "RUNNING"
    assert after.schedule_clock.started_at == before.schedule_clock.started_at
    assert not after.requires_replan
    with runtime.store.engine.connect() as tx:
        assert RuntimeRepository(tx).plan("clock", 1) == first.plan
    runtime.store.close()


def test_new_actions_do_not_start_before_publication_when_solver_takes_time(tmp_path):
    runtime, planner, _ = clock_service(tmp_path)
    before = tick(runtime, 150)

    class DelayedSolver:
        def solve(self, *args, **kwargs):
            runtime.clock.advance(152)
            return CpSatScheduler().solve(*args, **kwargs)

    planner.solver = DelayedSolver()
    recipe = runtime.knowledge.recipes[1]
    result = planner.apply_event(
        event(
            before,
            "delayed-add",
            "ADD_RECIPE",
            {"recipes": [{"id": recipe.recipe_id, "name": recipe.name}]},
            at=150,
        )
    )
    assert result.status == "PUBLISHED", result
    assert result.plan.committed_at == before.runtime.time_origin.at(152)
    assert all(a.interval.start_sec >= 152 for a in result.plan.validated.candidate.assignments)
    assert runtime.get("clock").runtime.executions == before.runtime.executions
    runtime.store.close()


def test_addition_uses_free_time_before_long_running_step_finishes(tmp_path):
    runtime, planner, _ = clock_service(tmp_path)
    before = tick(runtime, 150)
    active = next(e for e in before.runtime.executions if e.status == "RUNNING")
    recipe = runtime.knowledge.recipes[1]
    result = planner.apply_event(
        event(
            before,
            "add",
            "ADD_RECIPE",
            {"recipes": [{"id": recipe.recipe_id, "name": recipe.name}]},
            at=150,
        )
    )
    assert result.status == "PUBLISHED", result
    after = runtime.get("clock")
    assert after.runtime.executions == before.runtime.executions
    with runtime.store.engine.connect() as tx:
        problem = RuntimeRepository(tx).problem("clock", 2)
    new_tasks = {
        t.task_id
        for t in problem.logical_tasks
        if t.recipe_instance_id == after.menu[-1].recipe_instance_id
    }
    additions = [
        a for a in result.plan.validated.candidate.assignments if set(a.task_ids) & new_tasks
    ]
    assert min(a.interval.start_sec for a in additions) < active.task_spans[0].interval.end_sec
    assert all(a.interval.start_sec >= 150 for a in additions)
    runtime.store.close()


def test_zero_gap_continuation_remains_scheduled_once_after_immediate_publish(tmp_path):
    runtime, planner = layered_runtime(tmp_path, tight=True)
    before = tick(runtime, 60)
    result = request_replan(runtime, planner, 60)
    assert result.status == "PUBLISHED", result
    assert runtime.get("clock").runtime.executions == before.runtime.executions
    at120 = tick(runtime, 120)
    assert len(at120.runtime.executions) == 3
    assert sum(e.status == "RUNNING" for e in at120.runtime.executions) == 2
    final = tick(runtime, 180)
    assert len(final.runtime.executions) == 3
    assert all(e.status == "COMPLETED" for e in final.runtime.executions)
    runtime.store.close()


def test_publication_crossing_zero_gap_start_synchronizes_progress_and_retries(tmp_path):
    runtime, planner = layered_runtime(tmp_path, tight=True)
    tick(runtime, 119)

    class CrossingSolver:
        def solve(self, *args, **kwargs):
            runtime.clock.advance(121)
            return CpSatScheduler().solve(*args, **kwargs)

    planner.solver = CrossingSolver()
    result = request_replan(runtime, planner, 119)
    assert result.status == "PUBLISHED", result
    assert result.attempts == 2
    after = runtime.get("clock")
    assert len(after.runtime.executions) == 3
    assert sum(e.status == "RUNNING" for e in after.runtime.executions) == 2
    assert sorted(e.task_spans[0].interval.start_sec for e in after.runtime.executions) == [
        0,
        0,
        120,
    ]
    assert all(a.interval.start_sec >= 121 for a in result.plan.validated.candidate.assignments)
    runtime.store.close()


def test_short_maximum_lag_is_kept_when_request_budget_exceeds_its_window(tmp_path):
    knowledge = clock_knowledge()
    recipe = knowledge.recipes[0]
    recipe = recipe.model_copy(
        update={
            "dependencies": (
                recipe.dependencies[0].model_copy(update={"max_lag_sec": 2}),
                *recipe.dependencies[1:],
            )
        }
    )
    knowledge = knowledge.model_copy(update={"recipes": (recipe, *knowledge.recipes[1:])})
    runtime, planner, _ = clock_service(tmp_path, knowledge=knowledge)
    before = tick(runtime, 119)
    result = request_replan(runtime, planner, 119)
    assert result.status == "PUBLISHED", result
    assert runtime.get("clock").runtime.executions == before.runtime.executions
    assert min(a.interval.start_sec for a in result.plan.validated.candidate.assignments) <= 122
    runtime.store.close()
