"""提前准备声明的真实编译、物料派发、追加、重启恢复；隔离测试库。"""

from pathlib import Path

from app.domain.policy import SchedulingPolicy
from app.domain.runtime_facts import RuntimeDetails
from app.domain.runtime_session import RuntimeSession
from app.domain.schedule import CandidateSchedule
from app.knowledge.loader import read_release_ref
from app.knowledge.repository import SnapshotKnowledgeRepository
from app.runtime.clock import SimulationClock
from app.runtime.menu_events import apply_menu
from app.runtime.service import RuntimeService
from app.runtime.simulator import Simulator
from app.scheduling.cp_sat import CpSatScheduler
from app.services.planning import PlanningService
from app.storage.unit_of_work import UnitOfWork
from app.validation.advance_preparation import check_advance_preparations
from app.validation.schedule_context import Scan
from tests.compiler_support import runtime
from tests.runtime_support import event
from tests.unit.test_advance_preparation import compile_case, preparation_case


def test_declarations_survive_execution_append_and_restart(tmp_path):
    knowledge, seed = preparation_case()
    store = UnitOfWork(tmp_path / "advance-preparation.sqlite")
    store.migrate()
    clock = SimulationClock(seed.runtime.time_origin.start_at)
    service = RuntimeService(store, knowledge, clock)
    session = service.create_session("advance", "SIMULATED", clock.now(), seed.policy)
    planning = PlanningService(service, CpSatScheduler())
    recipe = knowledge.recipes[0]
    first = planning.apply_event(
        event(
            session,
            "first",
            "START_SESSION",
            {"recipes": [{"id": recipe.recipe_id, "name": recipe.name}]},
        )
    )
    assert first.status == "PUBLISHED", first
    assert first.plan.validated.candidate.metrics.makespan_sec == 1290
    simulator = Simulator(service, "advance")
    simulator.advance(1290)
    finished = service.get("advance")
    prepared = {
        task for item in finished.runtime.details.advance_preparations for task in item.task_ids
    }
    assert len(finished.runtime.executions) == 3
    assert all(
        record.status == "COMPLETED" and not set(record.task_ids) & prepared
        for record in finished.runtime.executions
    )
    assert all(
        occupancy.released_at is not None for occupancy in finished.runtime.details.occupancies
    )
    prepared_lot = next(lot for lot in finished.runtime.details.lots if lot.preparation_id)
    assert prepared_lot.available.numerator == 0
    assert prepared_lot.produced_by_execution_id is None
    addition = event(
        finished,
        "second",
        "ADD_RECIPE",
        {"recipes": [{"id": recipe.recipe_id, "name": recipe.name}]},
        at=2000,
    )
    second = planning.apply_event(addition)
    assert second.status == "PUBLISHED", second
    added = service.get("advance")
    assert added.runtime.executions == finished.runtime.executions
    assert len(added.runtime.details.advance_preparations) == 2
    assert added.runtime.details.advance_preparations[-1].available_at_sec == 2000
    assert all(
        assignment.interval.start_sec >= 2000
        for assignment in second.plan.validated.candidate.assignments
    )
    replay = service.apply_event(addition)
    assert replay.status == "APPLIED"
    assert service.get("advance") == added
    restarted = RuntimeService(store, knowledge, clock).get("advance")
    assert (
        restarted.runtime.details.advance_preparations == added.runtime.details.advance_preparations
    )
    store.close()


def test_current_100_recipe_preparation_bindings_compile_and_validate_sources():
    root = Path(__file__).resolve().parents[2]
    release_root = root / "data/preparations/p4-v1/releases"
    reference = read_release_ref(release_root, "delegated-v3-layered-devices-v1-all")
    repository = SnapshotKnowledgeRepository(release_root)
    repository.load(reference)
    with repository.acquire(reference) as lease:
        knowledge = lease.select(
            tuple(recipe.recipe_id for recipe in lease.loaded.snapshot.knowledge.recipes)
        )
    policy = SchedulingPolicy.model_validate_json(
        (root / "data/policies/p6-cook-prepared-v1.json").read_bytes()
    )
    assert len(knowledge.recipes) == 100
    assert len(policy.advance_preparation_rules) == 42
    failures = []
    for rule in policy.advance_preparation_rules:
        recipe = next(recipe for recipe in knowledge.recipes if recipe.recipe_id == rule.recipe_id)
        seed = RuntimeSession(
            runtime=runtime(knowledge, details=RuntimeDetails()),
            policy=policy,
            knowledge_release_id=reference.release_id,
        )
        try:
            session = apply_menu(
                seed,
                event(
                    seed,
                    "prepare-" + recipe.recipe_id.root,
                    "START_SESSION",
                    {"recipes": [{"id": recipe.recipe_id, "name": recipe.name}]},
                ),
                knowledge,
            )
            problem = compile_case(knowledge, session)
            candidate = CandidateSchedule(problem_hash=problem.problem_hash, assignments=())
            scan = Scan(knowledge, session.runtime, problem, candidate)
            check_advance_preparations(scan)
            assert not scan.issues, scan.issues
        except (ValueError, AssertionError) as exc:
            failures.append(f"{recipe.recipe_id.root} {recipe.name}: {exc}")
    assert not failures, "\n".join(failures)
