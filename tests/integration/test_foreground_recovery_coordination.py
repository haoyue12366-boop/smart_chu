"""在真实 HTTP 准入后插入恢复扫描，验证前台所有权及空闲恢复。"""

from fastapi.testclient import TestClient

from app.config import AppSettings
from app.domain.events import EventRecipe
from app.main import create_app
from app.services import competition
from app.storage.competition_tasks import HttpRequestRepository


def test_recovery_cannot_claim_admitted_foreground_request_and_idle_queue_still_recovers(
    tmp_path, monkeypatch
):
    app = create_app(
        AppSettings(
            database_path=tmp_path / "runtime.sqlite3",
            language_enabled=False,
            recovery_interval_sec=60,
        )
    )
    with TestClient(app) as client:
        services = app.state.container
        calls = []
        original_apply = services.planner.apply_event
        original_admit = competition.admit_menu

        def counted_apply(event, deadline=None):
            calls.append(event.event_id.root)
            return original_apply(event, deadline)

        def scan_after_admission(*args, **kwargs):
            record = original_admit(*args, **kwargs)
            # 插入真实恢复方法，SQL 准入已提交；此时前台尚未调用 execute_request。
            services.recover_pending()
            assert calls == [], "后台抢先执行了仍由 HTTP 请求持有的任务"
            return record

        monkeypatch.setattr(services.planner, "apply_event", counted_apply)
        monkeypatch.setattr(competition, "admit_menu", scan_after_admission)
        recipe = EventRecipe(id="61e6c51fec6e1d65587067e1", name="低温牛排")
        response = client.post(
            "/api/competition/plan?task_id=foreground-owner",
            json=[recipe.model_dump(mode="json")],
            headers={"Idempotency-Key": "one"},
        )
        assert response.status_code == 200, response.text
        assert len(calls) == 1 and services.foreground_requests == 0
        queued = original_admit(services, (recipe,), "idle-recovery", task_id="idle-recovery-task")
        services.recover_pending()
        with services.store.engine.connect() as tx:
            stored = HttpRequestRepository(tx).get(queued.request_id)
        assert stored is not None and stored.result.status == "PUBLISHED"
        assert len(calls) == 2
