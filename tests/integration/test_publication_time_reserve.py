"""合成耗时反例：已找到合法候选后，优化不能耗尽发布的真实时间。"""

import time

from app.domain.ports import Deadline
from app.domain.reports import SolveResult
from app.runtime.notifications import NotificationService
from app.scheduling.engine import PlanningEngine
from app.validation.schedule import ScheduleValidator
from tests.integration.test_menu_events_replanning import service, start_event
from tests.runtime_support import event
from tests.unit.test_schedule_validator import example


class OptimizationConsumer:
    """明确的耗时替身；完整候选由真实 Greedy 和独立 Validator 提供。"""

    def __init__(self):
        self.spent_ns = 0

    def solve(self, problem, hint, deadline, **kwargs):
        started = time.monotonic_ns()
        remaining = (deadline.expires_at_ns - started) / 1e9
        if remaining > 0:
            time.sleep(remaining)
        self.spent_ns += time.monotonic_ns() - started
        return SolveResult(
            status="UNKNOWN",
            problem_hash=problem.problem_hash,
            diagnostic_message="SYNTHETIC: consume optional optimization window",
        )


def test_engine_keeps_measured_validation_and_publication_time():
    knowledge, state, problem, _ = example()

    class SlowValidator:
        def validate(self, *args):
            # 模拟低算力上的扫描耗时，约束仍由真实独立校验器判定。
            time.sleep(0.2)
            return ScheduleValidator().validate(*args)

    consumer = OptimizationConsumer()
    deadline = Deadline(expires_at_ns=time.monotonic_ns() + 3_200_000_000)
    result = PlanningEngine(validator=SlowValidator(), solver=consumer).plan(
        problem, knowledge, state, deadline
    )
    assert result.status == "VALIDATED", result
    assert result.validation.valid
    assert consumer.spent_ns > 300_000_000
    # 不能只留下固定400ms：发布还有两次独立扫描和真实事务/归档。
    assert deadline.expires_at_ns - time.monotonic_ns() >= 900_000_000


def test_full_valid_witness_publishes_with_slow_notification_transaction(tmp_path, monkeypatch):
    runtime, planning, session = service(tmp_path)
    assert planning.apply_event(start_event(session)).status == "PUBLISHED"
    consumer = OptimizationConsumer()
    planning.solver = consumer
    original = NotificationService.persist

    def slow_persistence(self, tx, plan, state):
        # 合成慢写，仍在真正 SQLite 发布事务中，原子性和截止检查不替换。
        time.sleep(0.9)
        return original(self, tx, plan, state)

    monkeypatch.setattr(NotificationService, "persist", slow_persistence)
    before = runtime.get("flow")
    started = time.monotonic()
    result = planning.apply_event(
        event(
            before,
            "slow-publication-add",
            "ADD_RECIPE",
            {"recipes": [{"id": "synthetic-1", "name": "合成腌制1"}]},
        )
    )
    elapsed = time.monotonic() - started
    assert consumer.spent_ns > 700_000_000, "耗时替身必须真实消耗优化窗口"
    assert result.status == "PUBLISHED", result.model_dump_json()
    assert elapsed < 2.4
    assert result.budget_ms == 2400
    assert result.plan.validated.validation.valid
    assert len(result.plan.validated.candidate.recipe_completions) == 2
    committed = runtime.get("flow")
    assert committed.runtime.state_revision == 2 and committed.runtime.current_plan_version == 2
    assert committed.requires_replan is False
