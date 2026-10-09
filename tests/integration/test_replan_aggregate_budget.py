"""明确耗尽阶段预算的合成 Solver 验证跨版本冲突不能重新领取计算预算。"""

import time

from app.domain.reports import SolveResult
from app.scheduling.greedy import GreedyScheduler
from tests.integration.test_menu_events_replanning import service, start_event
from tests.runtime_support import event


class BudgetConsumer:
    """仅注入求解耗时和并发事件；候选仍由真实 Greedy 构造并独立校验。"""

    def __init__(self, runtime):
        self.runtime = runtime
        self.elapsed_ns = 0
        self.injected = False

    def solve(self, problem, hint, deadline, **kwargs):
        started = time.monotonic_ns()
        if not self.injected:
            self.injected = True
            current = self.runtime.get("flow")
            submitted = self.runtime.apply_event(
                event(
                    current,
                    "during-budget",
                    "ADD_RECIPE",
                    {
                        "recipes": [{"id": "synthetic-0", "name": "合成腌制0"}],
                    },
                )
            )
            assert submitted.status == "APPLIED"
        remaining = (deadline.expires_at_ns - time.monotonic_ns()) / 1e9
        if remaining > 0:
            time.sleep(min(remaining, 0.3))
        self.elapsed_ns += time.monotonic_ns() - started
        return SolveResult(
            status="UNKNOWN",
            problem_hash=problem.problem_hash,
            diagnostic_message="synthetic:consume phase allowance",
        )


def test_cas_retry_shares_solver_allowance_and_keeps_remaining_greedy_time(tmp_path):
    runtime, planning, session = service(tmp_path)
    assert planning.apply_event(start_event(session)).status == "PUBLISHED"
    solver = BudgetConsumer(runtime)
    planning.solver = solver
    current = runtime.get("flow")
    result = planning.apply_event(
        event(
            current,
            "start-budget",
            "ADD_RECIPE",
            {
                "recipes": [{"id": "synthetic-1", "name": "合成腌制1"}],
            },
        )
    )
    assert result.attempts == 2, result
    assert solver.elapsed_ns <= 1_550_000_000, solver.elapsed_ns
    assert result.status == "PUBLISHED", result.model_dump_json()
    assert len(result.plan.validated.candidate.recipe_completions) == 3
    assert result.plan.state_revision == runtime.get("flow").runtime.state_revision
    assert result.budget_ms == 2400


def test_cas_retry_retains_greedy_capacity_when_first_construction_uses_its_slice(
    tmp_path, monkeypatch
):
    runtime, planning, session = service(tmp_path)
    assert planning.apply_event(start_event(session)).status == "PUBLISHED"
    planning.solver = BudgetConsumer(runtime)
    original = GreedyScheduler.solve
    calls = []

    def consume_first_slice(self, problem, deadline):
        result = original(self, problem, deadline)
        calls.append((len(problem.recipe_instances), result.candidate is not None))
        if len(calls) == 1:
            # 模拟第一轮搜索继续到分配的截止时间；已经找到的真实候选保持不变。
            remaining = (deadline.expires_at_ns - time.monotonic_ns()) / 1e9
            if remaining > 0:
                time.sleep(remaining)
        return result

    monkeypatch.setattr(GreedyScheduler, "solve", consume_first_slice)
    current = runtime.get("flow")
    result = planning.apply_event(
        event(
            current,
            "consume-greedy-start",
            "ADD_RECIPE",
            {"recipes": [{"id": "synthetic-1", "name": "合成腌制1"}]},
        )
    )
    assert result.status == "PUBLISHED", result.model_dump_json()
    assert result.attempts == 2 and calls == [(2, True), (3, True)]
    assert len(result.plan.validated.candidate.recipe_completions) == 3
    assert result.plan.state_revision == runtime.get("flow").runtime.state_revision
    assert planning.solver.elapsed_ns <= 1_550_000_000
    assert result.budget_ms == 2400
