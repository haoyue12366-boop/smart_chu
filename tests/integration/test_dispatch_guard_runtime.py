"""真实 SQLite、Compiler/CP-SAT、事件、重排与恢复上的连续工艺链保护。"""

import time

from app.compiler.compiler import ProblemCompiler
from app.domain.base import content_hash
from app.domain.canonical_recipe import CanonicalRecipeModel
from app.domain.ports import Deadline
from app.domain.schedule import CandidateSchedule
from app.domain.scheduling_problem import SchedulingProblem
from app.runtime.clock import SimulationClock
from app.runtime.dispatch_guard import tight_chains
from app.runtime.recovery import restore_session
from app.runtime.service import RuntimeService
from app.runtime.simulator import Simulator
from app.scheduling.cp_sat import CpSatScheduler
from app.services.planning import PlanningService
from app.storage.unit_of_work import UnitOfWork
from app.validation.schedule import ScheduleValidator
from benchmarks.robustness.observed_session import start_session
from tests.runtime_support import ORIGIN, event, policy, synthetic_knowledge


def actual_service(tmp_path):
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
    selected = policy().model_copy(
        update={
            "policy_version": "synthetic-tight-chain-v1",
            "dispatch_guard_policy_id": "TIGHT_HUMAN_V1",
            "replan_budget": policy().replan_budget.model_copy(
                update={"total_ms": 3000, "solver_ms": 2100}
            ),
        }
    )
    store = UnitOfWork(tmp_path / "guard.sqlite")
    store.migrate()
    runtime = RuntimeService(store, knowledge, SimulationClock(ORIGIN))
    session = runtime.create_session("guard", "SIMULATED", ORIGIN, selected)
    planning = PlanningService(runtime, CpSatScheduler())
    outcome = planning.apply_event(
        event(
            session,
            "guard-start",
            "START_SESSION",
            {"recipes": [{"id": r.recipe_id, "name": r.name} for r in knowledge.recipes]},
        )
    )
    assert outcome.status == "PUBLISHED", outcome
    return runtime, planning


def test_runtime_guard_completes_after_real_replan_and_sqlite_restart(tmp_path):
    runtime, planning = actual_service(tmp_path)
    simulator = Simulator(runtime, "guard", seed=7)
    revisions = []
    for index in range(30):
        session = runtime.get("guard")
        complete = {t for e in session.runtime.executions for t in e.completed_task_ids}
        if len(complete) == 4:
            break
        actions = simulator._planned(session)
        assert actions, session
        events = simulator.advance(min(a.at for a in actions))
        assert events
        for observed in events:
            before = content_hash(runtime.get("guard"))
            duplicate = runtime.apply_event(observed)
            # 事务幂等入口重放首次回执，first_applied 字段仍属于该首次回执。
            assert duplicate.status == "APPLIED" and duplicate.event_id == observed.event_id
            assert content_hash(runtime.get("guard")) == before
        if runtime.get("guard").requires_replan:
            began = time.perf_counter_ns()
            result = planning.drain("guard")
            assert result.status == "PUBLISHED", result
            assert result.budget_ms == 3000
            assert (time.perf_counter_ns() - began) / 1_000_000 <= 3000
            revisions.append(result.plan.plan_version)
        if index == 1:
            latest = runtime.get("guard")
            clock = SimulationClock(ORIGIN)
            clock.advance(runtime.clock.offset_sec)
            restored = restore_session(
                runtime.store,
                "guard",
                clock,
                lambda release, knowledge=runtime.knowledge: knowledge,
            )
            assert content_hash(restored.get("guard")) == content_hash(latest)
            runtime = restored
            planning = PlanningService(runtime, CpSatScheduler())
            simulator = Simulator(runtime, "guard", seed=7)
    else:
        raise AssertionError("实际运行链未在有限事件内完成")
    session = runtime.get("guard")
    assert all(e.status == "COMPLETED" for e in session.runtime.executions)
    assert revisions, "此反例必须实际经过偏移触发重排"
    spans = {p.task_id: p.interval for e in session.runtime.executions for p in e.task_spans}
    finish_problem = ProblemCompiler().compile(
        runtime.knowledge,
        session.menu,
        session.runtime,
        session.policy,
        Deadline(expires_at_ns=time.monotonic_ns() + 4_200_000_000),
    )
    assert isinstance(finish_problem, SchedulingProblem), finish_problem
    proof = ScheduleValidator().validate(
        runtime.knowledge,
        session.runtime,
        finish_problem,
        CandidateSchedule(problem_hash=finish_problem.problem_hash, assignments=()),
    )
    assert proof.valid, proof
    first_instance = session.menu[0].recipe_instance_id
    first = {
        t.operation_id.root: spans[t.task_id]
        for t in finish_problem.logical_tasks
        if t.recipe_instance_id == first_instance
    }
    assert first["wait"].end_sec - first["wait"].start_sec == 60
    assert first["finish"].start_sec == first["wait"].end_sec
    assert all(o.released_at is not None for o in session.runtime.details.occupancies)


def test_pure_guard_cannot_observe_simulator_future_or_seed(tmp_path):
    runtime, _ = actual_service(tmp_path)
    simulator = Simulator(runtime, "guard", seed=1)
    session = runtime.get("guard")
    before = content_hash(session)
    choices = [(a.at, a.kind, a.group) for a in simulator._planned(session)]

    class ForbiddenFuture:
        def __iter__(self):
            raise AssertionError("保护逻辑读取了模拟器私有未来")

    simulator._future = ForbiddenFuture()
    simulator.seed = 999
    assert [(a.at, a.kind, a.group) for a in simulator._planned(session)] == choices
    assert content_hash(runtime.get("guard")) == before


def test_chain_closure_includes_external_bridge_to_avoid_self_wait():
    # A->C 为有限间隔，A->B->C 为另外一条真实路径；B 必须属于同一保护链。
    session = start_session(
        synthetic_knowledge(),
        policy(),
        {"case_id": "bridge", "recipe_ids": ["synthetic-0"]},
        ORIGIN,
    )
    assert tight_chains(session, (("a", "c", 0, 10), ("a", "b", 0, None), ("b", "c", 0, None))) == (
        frozenset({"a", "b", "c"}),
    )


def test_default_policy_omits_optional_guard_from_archived_json():
    assert "dispatch_guard_policy_id" not in policy().model_dump(mode="json")
