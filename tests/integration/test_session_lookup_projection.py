"""运行时选择只读绑定字段，模拟时钟仍读取数据库当前偏移。"""

import pytest

from app.config import AppSettings
from app.domain.policy import SchedulingPolicy
from app.runtime.clock import SimulationClock
from app.runtime.service import RuntimeService
from app.scheduling.cp_sat import CpSatScheduler
from app.services.container import ServiceContainer
from app.services.planning import PlanningService
from app.storage.repositories import RuntimeRepository
from app.storage.unit_of_work import UnitOfWork
from tests.integration.test_schedule_clock import clock_knowledge
from tests.runtime_support import ORIGIN


def services_for(tmp_path, mode):
    services = ServiceContainer(AppSettings(database_path=tmp_path / "lookup.sqlite3"))
    services.store = UnitOfWork(services.settings.database_path)
    services.store.migrate()
    runtime = RuntimeService(services.store, clock_knowledge(), SimulationClock(ORIGIN))
    session = runtime.create_session(
        "lookup", mode, ORIGIN, SchedulingPolicy(policy_version="synthetic-lookup")
    )
    services.runtimes[session.knowledge_release_id] = runtime
    services.planners[session.knowledge_release_id] = PlanningService(runtime, CpSatScheduler())
    return services, runtime


@pytest.mark.parametrize("mode", ["MANUAL_CONFIRM", "SCHEDULE_CLOCK"])
def test_runtime_selection_does_not_decode_large_session_body(tmp_path, monkeypatch, mode):
    services, runtime = services_for(tmp_path, mode)

    def unexpected_decode(*args):
        raise AssertionError("选择运行时不应反序列化全桌工序、物料和历史")

    monkeypatch.setattr(RuntimeRepository, "get", unexpected_decode)
    try:
        assert services.for_session("lookup")[0] is runtime
        with pytest.raises(KeyError):
            services.for_session("missing")
    finally:
        services.close()


def test_simulated_runtime_refreshes_persisted_clock_offset(tmp_path):
    services, _ = services_for(tmp_path, "SIMULATED")
    try:
        runtime, planner = services.for_session("lookup")
        assert runtime.clock.offset_sec == 0
        with services.store.transaction() as tx:
            repo = RuntimeRepository(tx)
            old = repo.get("lookup")
            repo.save(
                old.model_copy(
                    update={"runtime": old.runtime.model_copy(update={"now_offset_sec": 30})}
                ),
                expected_revision=0,
                expected_plan=0,
            )
        again, same_planner = services.for_session("lookup")
        assert again is runtime and same_planner is planner
        assert again.clock.offset_sec == 30
    finally:
        services.close()
