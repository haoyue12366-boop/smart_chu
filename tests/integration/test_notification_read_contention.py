"""合成工艺、真实 SQLite 写锁，验证无到期通知时读取不争抢写事务。"""

from types import SimpleNamespace

from app.services.notification_dispatcher import NotificationDispatcher
from tests.integration.test_schedule_clock import clock_service


def test_idle_notification_poll_reads_committed_stream_while_writer_is_busy(tmp_path):
    runtime, planner, _ = clock_service(tmp_path)
    services = SimpleNamespace(store=runtime.store, for_session=lambda _: (runtime, planner))
    dispatcher = NotificationDispatcher(services)
    try:
        messages = dispatcher.poll("clock")
        assert messages and any(m.kind == "PLAN_CHANGED" for m in messages)
        cursor = messages[-1].cursor
        with runtime.store.transaction():
            assert dispatcher.poll("clock", cursor) == ()
            assert dispatcher.poll("clock") == messages
    finally:
        runtime.store.close()
