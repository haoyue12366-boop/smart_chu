"""已知数值、动态事实、独立参考与重启后的五字段输出。"""

import json
from decimal import ROUND_HALF_UP, Decimal

import pytest
from fastapi.testclient import TestClient

from app.api.competition_adapter import CompetitionAdapter
from app.config import ROOT, AppSettings
from app.domain.candidates import stable_id
from app.domain.reports import SolveResult
from app.main import create_app
from app.runtime.clock import SimulationClock
from app.runtime.service import RuntimeService
from app.runtime.simulator import Simulator
from app.scheduling.cp_sat import CpSatScheduler
from app.scheduling.greedy import GreedyScheduler
from app.scheduling.metrics import compute_metrics
from app.scheduling.serial_reference import serial_order_holds
from app.services.planning import PlanningService
from app.storage.competition_tasks import HttpRequestRepository
from app.storage.repositories import RuntimeRepository
from app.storage.unit_of_work import UnitOfWork
from app.validation.schedule import ScheduleValidator
from tests.runtime_support import ORIGIN, event, p4_knowledge, policy, synthetic_knowledge

FIELDS = {"overview", "cookingTimeline", "ingredientsSummary", "detailTimeline", "recipeDetail"}


def service(tmp_path, budget, knowledge=None):
    selected = policy().model_copy(
        update={
            "policy_version": f"synthetic-timesave-v1:{budget}",
            "replan_search_mode": "FEASIBILITY_FIRST",
            "replan_budget": policy().replan_budget.model_copy(update={"total_ms": budget}),
        }
    )
    store = UnitOfWork(tmp_path / "timesave.sqlite")
    store.migrate()
    runtime = RuntimeService(store, knowledge or synthetic_knowledge(), SimulationClock(ORIGIN))
    session = runtime.create_session("timesave", "SIMULATED", ORIGIN, selected)
    planning = PlanningService(runtime, CpSatScheduler())
    initial = planning.apply_event(
        event(
            session,
            "start",
            "START_SESSION",
            {
                "recipes": [{"id": "synthetic-0", "name": "合成腌制0"}],
            },
        )
    )
    assert initial.status == "PUBLISHED", initial
    return runtime, planning


def add(runtime, planning, at=0):
    request = event(
        runtime.get("timesave"),
        "add",
        "ADD_RECIPE",
        {
            "recipes": [{"id": "synthetic-1", "name": "合成腌制1"}],
        },
        at=at,
    )
    result = planning.apply_event(request)
    assert result.status == "PUBLISHED", result
    assert result.elapsed_ms < result.budget_ms
    assert any(t.stage == "FEEDBACK_FEASIBILITY_RETURN" for t in result.planning.timings)
    assert not result.planning.human_objective_optimized
    assert not result.planning.total_human_objective_optimized
    assert not result.planning.stability_objective_optimized
    return result, request


def project(runtime, plan):
    with runtime.store.engine.connect() as tx:
        repo = RuntimeRepository(tx)
        assert repo.plan("timesave", plan.plan_version) == plan
        problem = repo.problem("timesave", plan.plan_version)
    reference = plan.serial_reference
    assert reference is not None, "快速发布必须携带当前状态的真实串行参考"
    assert reference.validation.valid
    assert serial_order_holds(reference.candidate, problem)
    assert (
        reference.candidate.problem_hash
        == plan.validated.candidate.problem_hash
        == problem.problem_hash
    )
    assert plan.time_origin == problem.runtime.time_origin
    assert plan.state_revision == problem.runtime.state_revision
    assert plan.knowledge_version == problem.knowledge_version
    assert plan.snapshot_id == problem.snapshot_id
    validator = ScheduleValidator()
    for validated in (plan.validated, reference):
        assert validator.validate(
            runtime.knowledge, problem.runtime, problem, validated.candidate
        ).valid
        assert validated.candidate.metrics.makespan_sec == max(
            (r.completion_sec for r in validated.candidate.recipe_completions), default=0
        )
    saved = reference.candidate.metrics.makespan_sec - plan.validated.candidate.metrics.makespan_sec
    assert saved >= 0
    expected = format((Decimal(saved) / 60).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP), ".1f")
    response = CompetitionAdapter().to_response(plan, problem, runtime.knowledge)
    assert set(response.model_dump(mode="json")) == FIELDS
    assert response.overview.timeSave == expected
    return response, problem


@pytest.mark.parametrize("budget", [2400, 3000])
def test_fast_parallel_addition_has_known_twenty_one_minutes_saved(tmp_path, budget):
    runtime, planning = service(tmp_path, budget)
    result, request = add(runtime, planning)
    response, problem = project(runtime, result.plan)
    # 两道 180 秒人工 + 1200 秒等待 + 60 秒人工：逐道 2880 秒，合法并行 1620 秒。
    assert result.plan.serial_reference.candidate.metrics.makespan_sec == 2880
    assert result.plan.validated.candidate.metrics.makespan_sec == 1620
    assert response.overview.timeSave == "21.0"
    assert not serial_order_holds(result.plan.validated.candidate, problem)
    assert planning.apply_event(request).status == "NO_REPLAN"
    assert project(runtime, result.plan)[0] == response
    runtime.store.close()


@pytest.mark.parametrize("budget", [2400, 3000])
def test_cancel_rebuilds_reference_and_last_cancel_returns_real_zero(tmp_path, budget):
    runtime, planning = service(tmp_path, budget)
    added, _ = add(runtime, planning)
    old_hash = added.plan.serial_reference.candidate.problem_hash
    current = runtime.get("timesave")
    cancelled = planning.apply_event(
        event(
            current,
            "cancel-second",
            "CANCEL_RECIPE",
            {
                "recipe_instance_id": current.menu[1].recipe_instance_id,
            },
        )
    )
    assert cancelled.status == "PUBLISHED", cancelled
    response, problem = project(runtime, cancelled.plan)
    assert response.overview.recipeCount == 1 and response.overview.timeSave == "0.0"
    assert problem.problem_hash != old_hash
    current = runtime.get("timesave")
    empty = planning.apply_event(
        event(
            current,
            "cancel-last",
            "CANCEL_RECIPE",
            {
                "recipe_instance_id": current.menu[0].recipe_instance_id,
            },
        )
    )
    assert empty.status == "PUBLISHED", empty
    response, _ = project(runtime, empty.plan)
    assert response.overview.recipeCount == 0 and response.overview.timeSave == "0.0"
    runtime.store.close()


@pytest.mark.parametrize("budget", [2400, 3000])
def test_completed_and_running_facts_duration_update_and_restart_keep_time_save(tmp_path, budget):
    runtime, planning = service(tmp_path, budget)
    Simulator(runtime, "timesave").advance(200)
    before = runtime.get("timesave")
    completed = next(e for e in before.runtime.executions if e.status == "COMPLETED")
    running = next(e for e in before.runtime.executions if e.status == "RUNNING")
    added, _ = add(runtime, planning, at=200)
    _, old_problem = project(runtime, added.plan)
    assert (
        next(e for e in old_problem.fixed_executions if e.execution_id == completed.execution_id)
        == completed
    )
    frozen = next(e for e in old_problem.fixed_executions if e.execution_id == running.execution_id)
    assert frozen.started_at == running.started_at == ORIGIN.replace(minute=3)
    current = runtime.get("timesave")
    updated = event(
        current,
        "remaining-update",
        "DURATION_UPDATED",
        {
            "task_id": running.task_ids[0],
            "execution_id": running.execution_id,
            "remaining_sec": 1500,
        },
        at=200,
    )
    changed = planning.apply_event(updated)
    assert changed.status == "PUBLISHED", changed
    response, problem = project(runtime, changed.plan)
    assert changed.elapsed_ms < budget
    assert problem.problem_hash != old_problem.problem_hash
    new_running = next(
        e for e in problem.fixed_executions if e.execution_id == running.execution_id
    )
    assert new_running.started_at == running.started_at and new_running.remaining_sec == 1500
    assert planning.apply_event(updated).status == "NO_REPLAN"
    state = runtime.get("timesave")
    path, knowledge = runtime.store.path, runtime.knowledge
    runtime.store.close()
    restarted = RuntimeService(UnitOfWork(path), knowledge, SimulationClock(ORIGIN))
    assert restarted.get("timesave") == state
    assert project(restarted, changed.plan)[0] == response
    restarted.store.close()


@pytest.mark.parametrize("invalid_parallel", [False, True])
def test_missing_or_parallel_impostor_reference_blocks_publication(tmp_path, invalid_parallel):
    class UnavailableReferenceSolver:
        def solve(self, problem, hint, deadline, **kwargs):
            assert kwargs["serial_menu"]
            if invalid_parallel:
                parallel = GreedyScheduler().solve(problem, deadline).candidate
                assert parallel is not None and not serial_order_holds(parallel, problem)
                return SolveResult(
                    status="FEASIBLE", problem_hash=problem.problem_hash, candidate=parallel
                )
            return SolveResult(status="UNKNOWN", problem_hash=problem.problem_hash)

    runtime, _ = service(tmp_path, 2400)
    current = runtime.get("timesave")
    applied = runtime.apply_event(
        event(
            current,
            "add",
            "ADD_RECIPE",
            {
                "recipes": [{"id": "synthetic-1", "name": "合成腌制1"}],
            },
        )
    )
    assert applied.requires_replan
    outcome = PlanningService(runtime, UnavailableReferenceSolver()).drain("timesave")
    assert outcome.status == "FAILED", outcome
    assert outcome.planning.failure.code == "STATE_INCOMPLETE"
    assert outcome.plan is None
    assert runtime.get("timesave").dispatch_blocked
    assert runtime.get("timesave").runtime.current_plan_version == 1
    runtime.store.close()


@pytest.mark.parametrize("budget", [2400, 3000])
def test_real_competition_http_add_replay_and_application_restart(tmp_path, budget):
    from app.domain.policy import SchedulingPolicy

    version = "v1" if budget == 2400 else "v2"
    selected = SchedulingPolicy.model_validate_json(
        (ROOT / f"data/policies/p6-feedback-fast-{version}.json").read_bytes()
    )
    assert selected.replan_budget.total_ms == budget
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(selected.model_dump_json(), encoding="utf-8")
    settings = AppSettings(database_path=tmp_path / "real.sqlite", policy_path=policy_path)
    suite = json.loads((ROOT / "benchmarks/scenarios/full_suite.json").read_bytes())
    case = next(c for c in suite["combinations"] if c["case_id"] == "combination-000")
    recipes = {r.recipe_id.root: r for r in p4_knowledge().recipes}
    menu = [{"id": rid, "name": recipes[rid].name} for rid in case["recipe_ids"]]
    url = "/api/competition/plan?task_id=timesave-real"
    headers = {"Idempotency-Key": "add"}
    with TestClient(create_app(settings)) as client:
        initial = client.post(url, json=menu[:-1], headers={"Idempotency-Key": "initial"})
        assert initial.status_code == 200, initial.text
        added = client.post(url, json=menu[-1:], headers=headers)
        assert added.status_code == 200, added.text
        assert set(added.json()) == FIELDS
        assert float(added.headers["Server-Timing"].split("=")[1]) <= budget
        assert client.post(url, json=menu[-1:], headers=headers).text == added.text
        services = client.app.state.container
        with services.store.engine.connect() as tx:
            receipt = HttpRequestRepository(tx).get(
                stable_id("competition-http", "timesave-real", "add")
            )
            plan = RuntimeRepository(tx).plan(receipt.session_id, receipt.result.plan.plan_version)
            problem = RuntimeRepository(tx).problem(receipt.session_id, plan.plan_version)
        assert plan.serial_reference is not None
        assert any(
            t.stage == "FEEDBACK_FEASIBILITY_RETURN" for t in receipt.result.planning.timings
        )
        assert (
            CompetitionAdapter()
            .to_response(plan, problem, services.knowledge_for(receipt.session_id))
            .overview.timeSave
            == added.json()["overview"]["timeSave"]
        )

    with TestClient(create_app(settings)) as restarted:
        replayed = restarted.post(url, json=menu[-1:], headers=headers)
        assert replayed.status_code == 200 and replayed.text == added.text
        services = restarted.app.state.container
        with services.store.engine.connect() as tx:
            restored = RuntimeRepository(tx).plan(receipt.session_id, plan.plan_version)
            restored_problem = RuntimeRepository(tx).problem(receipt.session_id, plan.plan_version)
        assert restored == plan and restored_problem == problem
        assert (
            CompetitionAdapter()
            .to_response(restored, restored_problem, services.knowledge_for(receipt.session_id))
            .overview.timeSave
            == added.json()["overview"]["timeSave"]
        )


def test_shorter_serial_reference_is_selected_as_real_plan_without_clamping(tmp_path, monkeypatch):
    knowledge = synthetic_knowledge()
    # 独立的 1800 秒被动准备使合法时间域足以包含较慢候选；不修改生产约束。
    recipes = []
    for recipe in knowledge.recipes:
        value = recipe.model_dump(mode="json")
        soak = {**value["operations"][1], "operation_id": "soak"}
        soak["duration"] = {**soak["duration"], "execution_sec": 1800}
        value["operations"].append(soak)
        recipes.append(type(recipe).model_validate(value))
    knowledge = knowledge.model_copy(update={"recipes": tuple(recipes)})
    runtime, planning = service(tmp_path, 2400, knowledge)
    original = GreedyScheduler.solve

    def delayed_greedy(self, problem, deadline):
        result = original(self, problem, deadline)
        candidate = result.candidate
        assert candidate is not None
        shifted = candidate.model_copy(
            update={
                "assignments": tuple(
                    a.model_copy(
                        update={
                            "interval": a.interval.model_copy(
                                update={
                                    "start_sec": a.interval.start_sec + 1800,
                                    "end_sec": a.interval.end_sec + 1800,
                                }
                            ),
                        }
                    )
                    for a in candidate.assignments
                ),
                "recipe_completions": tuple(
                    r.model_copy(update={"completion_sec": r.completion_sec + 1800})
                    for r in candidate.recipe_completions
                ),
            }
        )
        shifted = shifted.model_copy(update={"metrics": compute_metrics(shifted, problem)})
        assert ScheduleValidator().validate(knowledge, problem.runtime, problem, shifted).valid
        return result.model_copy(update={"candidate": shifted})

    monkeypatch.setattr(GreedyScheduler, "solve", delayed_greedy)
    changed, _ = add(runtime, planning)
    response, _ = project(runtime, changed.plan)
    assert changed.planning.selected_candidate_source == "CP_SAT"
    assert changed.plan.validated.candidate == changed.plan.serial_reference.candidate
    assert changed.plan.validated.candidate.metrics.makespan_sec == 3600
    assert response.overview.timeSave == "0.0"
    runtime.store.close()
