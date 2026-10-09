"""启动上下文有限重试；测试替身只验证准备生命周期，不作为求解证据。"""

import pytest

from app.scheduling.worker import SolverWorker


@pytest.mark.parametrize("eventually_ready", [True, False])
def test_context_retries_one_failed_preparation_and_closes_each_attempt(
    eventually_ready, monkeypatch
):
    worker = SolverWorker()
    calls, closes = [], []

    def readiness(deadline=None):
        calls.append(deadline)
        return len(calls) == 2 and eventually_ready

    monkeypatch.setattr(worker, "warmup", readiness)
    monkeypatch.setattr(worker, "close", lambda: closes.append(True))
    if eventually_ready:
        with worker as prepared:
            assert prepared is worker
    else:
        with pytest.raises(TimeoutError, match="冷启动失败"):
            worker.__enter__()
    assert len(calls) == 2 and len(closes) == 2
    assert worker.restart_count == 1
