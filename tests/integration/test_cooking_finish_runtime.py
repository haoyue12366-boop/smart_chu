"""出锅时间沿用真实成员端口和执行事实，不把库存接收时刻冒充烹饪时刻。"""

import time

from app.compiler.compiler import ProblemCompiler
from app.config import AppSettings
from app.domain.cooking_completion import CookingCompletionRule
from app.domain.policy import SchedulingPolicy
from app.domain.ports import Deadline
from app.domain.recipe_context import RecipeSchedulingContext
from app.domain.reports import CompilationFailure
from app.knowledge.loader import read_release_ref
from app.knowledge.repository import SnapshotKnowledgeRepository


def new_knowledge():
    settings = AppSettings()
    ref = read_release_ref(settings.release_root, settings.release_id)
    repo = SnapshotKnowledgeRepository(settings.release_root)
    repo.load(ref)
    with repo.acquire(ref) as lease:
        return lease.select(tuple(r.recipe_id for r in lease.loaded.snapshot.knowledge.recipes))


def test_shared_cooking_uses_member_unload_offsets_and_preserves_running_batch(
    tmp_path, monkeypatch
):
    from app.scheduling.cp_sat import CpSatScheduler
    from tests.integration import test_running_thermal_batch as support
    from tests.runtime_support import event

    monkeypatch.setattr(support, "p4_knowledge", new_knowledge)
    runtime, planning, _, binding, record, _ = support.running_h02(tmp_path, CpSatScheduler())
    current = runtime.get("flow")
    result = planning.apply_event(
        event(
            current,
            "cooking-add",
            "ADD_RECIPE",
            {"recipes": [{"id": current.menu[0].recipe_id, "name": current.menu[0].name}]},
            at=runtime.clock.offset_sec,
        )
    )
    assert result.status == "PUBLISHED", result
    after = runtime.get("flow")
    assert (
        next(e for e in after.runtime.executions if e.execution_id == record.execution_id) == record
    )
    ends = {
        f.recipe_instance_id: f.cooking_finish_sec
        for f in result.plan.validated.candidate.metrics.recipe_cooking_finishes
    }
    assert len(ends) == 3  # 相同菜谱也按实例区分。
    for instance in current.menu:
        unload = next(
            o for o in runtime.knowledge.recipe_contexts if o.recipe_id == instance.recipe_id
        ).cooking_completion.operation_ids[0]
        from app.domain.candidates import stable_id

        task_id = stable_id("task", instance.recipe_instance_id.root, unload.root)
        span = next(s for s in binding.task_spans if s.task_id.root == task_id)
        assert ends[instance.recipe_instance_id] == span.interval.end_sec
    # H02的两个取出端口同刻结束，但仍保留两个实例身份。
    assert len(current.menu) == 2
    runtime.store.close()


def test_completed_cooking_is_fixed_after_adding_another_dish(tmp_path):
    from app.runtime.clock import SimulationClock
    from app.runtime.service import RuntimeService
    from app.runtime.simulator import Simulator
    from app.scheduling.cp_sat import CpSatScheduler
    from app.services.planning import PlanningService
    from app.storage.unit_of_work import UnitOfWork
    from tests.runtime_support import ORIGIN, event
    from tests.unit.test_cooking_completion import cooking_case

    knowledge, problem = cooking_case()
    store = UnitOfWork(tmp_path / "cooking.sqlite")
    store.migrate()
    runtime = RuntimeService(store, knowledge, SimulationClock(ORIGIN))
    session = runtime.create_session("cooking", "SIMULATED", ORIGIN, problem.policy)
    planning = PlanningService(runtime, CpSatScheduler())
    first = knowledge.recipes[0]
    result = planning.apply_event(
        event(
            session,
            "start",
            "START_SESSION",
            {"recipes": [{"id": first.recipe_id, "name": first.name}]},
        )
    )
    assert result.status == "PUBLISHED", result
    original = result.plan.validated.candidate.metrics.recipe_cooking_finishes[0]
    Simulator(runtime, "cooking").advance(result.plan.validated.candidate.metrics.makespan_sec)
    current = runtime.get("cooking")
    facts = current.runtime.executions
    second = knowledge.recipes[1]
    added = planning.apply_event(
        event(
            current,
            "add",
            "ADD_RECIPE",
            {"recipes": [{"id": second.recipe_id, "name": second.name}]},
            at=runtime.clock.offset_sec,
        )
    )
    assert added.status == "PUBLISHED", added
    assert all(e in runtime.get("cooking").runtime.executions for e in facts)
    metrics = added.plan.validated.candidate.metrics
    assert (
        next(
            f
            for f in metrics.recipe_cooking_finishes
            if f.recipe_instance_id == original.recipe_instance_id
        )
        == original
    )
    assert metrics.cooking_finish_spread_sec > 300
    assert metrics.remaining_cooking_finish_spread_sec == 0
    store.close()


def test_inventory_without_cooking_timestamp_cannot_replace_cooking_anchor(tmp_path):
    from app.runtime.replanning import prepare_replan
    from tests.integration.test_inventory_substitution import stock_service

    runtime, _, _, _, _ = stock_service(tmp_path)
    session = runtime.get("inventory")
    contexts = tuple(
        RecipeSchedulingContext(
            recipe_id=r.recipe_id,
            cooking_completion=CookingCompletionRule(
                operation_ids=["cut" if r.recipe_id.root.endswith("target") else "source-cut"],
                kind="NO_HEAT_READY",
                evidence_refs=["synthetic:inventory"],
            ),
        )
        for r in runtime.knowledge.recipes
    )
    knowledge = runtime.knowledge.model_copy(update={"recipe_contexts": contexts})
    policy = SchedulingPolicy.model_validate_json(AppSettings().policy_path.read_bytes())
    request = prepare_replan(session, None, knowledge, policy)
    result = ProblemCompiler().compile(
        knowledge,
        request.menu,
        request.runtime,
        policy,
        Deadline(expires_at_ns=time.monotonic_ns() + 5_000_000_000),
    )
    assert isinstance(result, CompilationFailure)
    assert "库存" in result.message and "出锅" in result.message
    runtime.store.close()
