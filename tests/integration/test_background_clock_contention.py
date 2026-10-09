"""真实时钟、计划与 SQLite；仅容器绑定使用隔离的合成菜谱运行时。"""

from app.config import AppSettings
from app.services.container import ServiceContainer
from tests.integration.test_schedule_clock import clock_service, tick


def test_background_idle_clock_does_not_compete_with_foreground_writer(
    tmp_path, monkeypatch, caplog
):
    runtime, planner, _ = clock_service(tmp_path)
    services = ServiceContainer(AppSettings(database_path=runtime.store.path))
    services.store = runtime.store
    monkeypatch.setattr(services, "for_session", lambda _: (runtime, planner))
    try:
        started = tick(runtime, 0)
        assert any(e.status == "RUNNING" for e in started.runtime.executions)
        runtime.clock.advance(30)
        with runtime.store.transaction():
            services.recover_pending()
        assert not [r for r in caplog.records if r.exc_info], "空闲时钟不应申请写锁并反复报错"
        # 前台仍显式同步到当前时间，不能因后台缓存而漏掉30秒进度。
        current = services.advance_clock("clock")
        assert current.runtime.now_offset_sec == 30
        assert current.runtime.executions == started.runtime.executions
        runtime.clock.advance(120)
        services.recover_pending()
        completed = runtime.get("clock")
        assert any(e.status == "COMPLETED" for e in completed.runtime.executions)
        services.recover_pending()
        assert runtime.get("clock").runtime.executions == completed.runtime.executions
    finally:
        services.close()
