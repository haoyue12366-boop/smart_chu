"""合成耗时反例：已找到合法候选后，优化不能耗尽发布的真实时间。"""

import time

from app.domain.reports import SolveResult
from app.runtime.notifications import NotificationService
from tests.integration.test_menu_events_replanning import service, start_event
from tests.runtime_support import event


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
