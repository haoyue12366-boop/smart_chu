"""真实常驻子进程及全链路期限；不依赖 Neo4j、LLM 或运行数据库。"""

import time

from app.domain.ports import PlanningRequest
from app.scheduling.worker import SolverWorker
from app.services.planning_core import PlanningCore
from tests.compiler_support import menu_for, published_knowledge, runtime
from tests.unit.test_cp_sat_model import deadline
from tests.unit.test_schedule_validator import example


def test_worker_is_reused_and_returns_real_solver_result():
    _, _, problem, _ = example()
    with SolverWorker() as worker:
        pid = worker.process_id
        first = worker.solve(problem, None, deadline())
        second = worker.solve(problem, first.candidate, deadline())
        assert first.status == second.status == "OPTIMAL"
        assert worker.process_id == pid
        assert worker.last_build_report.problem_hash == problem.problem_hash
    assert not worker.is_alive


def test_planning_core_uses_one_deadline_and_exports_full_validated_problem():
    knowledge = published_knowledge()
    recipe = next(r for r in knowledge.recipes if r.name == "亲朋欢聚套餐")
    from app.domain.policy import SchedulingPolicy

    request = PlanningRequest(
        request_id="real-budget-test",
        menu=menu_for(recipe),
        policy=SchedulingPolicy(policy_version="p2-budget-test"),
    )
    with SolverWorker() as worker:
        core = PlanningCore(solver=worker)
        started = time.monotonic()
        result = core.compute(request, knowledge, runtime(), deadline(4.2))
        elapsed = time.monotonic() - started
        assert result.status == "VALIDATED", result
        assert result.validation.valid
        assert elapsed < 4.2
        assert core.last_problem.problem_hash == result.candidate.problem_hash
        assert {t.stage for t in result.timings} >= {"COMPILATION", "PLANNING_CORE"}


def test_expired_request_never_starts_a_solver_process():
    knowledge = published_knowledge()
    from app.domain.policy import SchedulingPolicy

    worker = SolverWorker()
    request = PlanningRequest(
        request_id="expired",
        menu=menu_for(knowledge.recipes[0]),
        policy=SchedulingPolicy(policy_version="expired"),
    )
    result = PlanningCore(solver=worker).compute(request, knowledge, runtime(), deadline(0))
    assert result.status == "FAILED"
    assert result.candidate is None
    assert worker.process_id is None


def test_real_worker_short_stage_preserves_readiness_for_next_request():
    from tests.unit.test_problem_compilation import compile_menu

    knowledge = published_knowledge()
    recipe = next(r for r in knowledge.recipes if r.name == "低温牛排")
    problem = compile_menu(recipe)
    with SolverWorker() as worker:
        pid = worker.process_id
        for _ in range(3):
            worker.solve(problem, None, deadline(0.3), serial_menu=True)
            assert worker.is_alive
            assert worker.process_id == pid
        final = worker.solve(problem, None, deadline(2))
        assert final.candidate is not None
