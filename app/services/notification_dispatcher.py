"""应用编排持久通知投递；观察通知不会生成执行事实。"""

import json

from sqlalchemy import Connection, exists, select

from app.domain.base import FrozenModel
from app.domain.runtime_session import NotificationRecord
from app.domain.schedule import PublishedPlan
from app.runtime.notification_templates import operation_notice, plan_change_notice
from app.runtime.notifications import NotificationService
from app.services.container import ServiceContainer
from app.storage import models
from app.storage.notification_stream import append_message
from app.storage.repositories import RuntimeRepository


class StreamMessage(FrozenModel):
    cursor: int
    notification_id: str
    event_id: str
    plan_version: int
    kind: str
    text: str
    data: dict[str, object]


class NotificationDispatcher:
    def __init__(self, services: ServiceContainer) -> None:
        self.services = services

    def poll(self, session_id: str, after: int = 0) -> tuple[StreamMessage, ...]:
        runtime, _ = self.services.for_session(session_id)
        missing_query = (
            select(models.plans.c.body)
            .where(
                models.plans.c.session_id == session_id,
                ~exists(
                    select(models.notification_stream.c.cursor).where(
                        models.notification_stream.c.deduplication_key
                        == "plan-changed:" + models.plans.c.publication_id
                    )
                ),
            )
            .order_by(models.plans.c.version)
        )
        # SSE 的空轮询只读已提交记录，不申请 SQLite 唯一写锁。
        # 有到期通知或历史补档时才进入写事务，并在锁内重新读取、核验。
        with self.services.store.engine.connect() as tx:
            missing = tx.execute(missing_query.limit(1)).first()
            due = NotificationService().due(tx, session_id, runtime.clock.now())
            if missing is None and not due:
                return self._messages(tx, session_id, after)
        with self.services.store.transaction() as tx:
            repo = RuntimeRepository(tx)
            repo.get(session_id)
            # 旧数据库升级后仅补充尚未归档的历史发布；新计划已原子写入游标流。
            for body in tx.execute(missing_query).scalars():
                self._plan_notice(tx, session_id, PublishedPlan.model_validate_json(body))
            service = NotificationService()
            for notice in service.due(tx, session_id, runtime.clock.now()):
                problem = repo.problem(session_id, notice.plan_version)
                plan = repo.plan(session_id, notice.plan_version)
                if plan is None:
                    raise ValueError("通知绑定的发布计划不存在")
                text = operation_notice(problem, notice.task_ids, notice.kind, plan)
                record = service.mark_sent(tx, notice.notification_id, runtime.clock.now())
                self._notice(tx, record, text)
            return self._messages(tx, session_id, after)

    @staticmethod
    def _messages(tx: Connection, session_id: str, after: int) -> tuple[StreamMessage, ...]:
        rows = tx.execute(
            select(models.notification_stream.c.cursor, models.notification_stream.c.body)
            .where(
                models.notification_stream.c.session_id == session_id,
                models.notification_stream.c.cursor > after,
            )
            .order_by(models.notification_stream.c.cursor)
            .limit(200)
        ).all()
        return tuple(StreamMessage(cursor=row.cursor, **json.loads(row.body)) for row in rows)

    @staticmethod
    def _plan_notice(tx: Connection, sid: str, plan: PublishedPlan) -> None:
        identity = "plan-changed:" + plan.publication_id
        append_message(tx, sid, identity, plan_change_notice(plan))

    @staticmethod
    def _notice(tx: Connection, record: NotificationRecord, text: str) -> None:
        append_message(
            tx,
            record.session_id,
            record.deduplication_key,
            {
                "notification_id": record.notification_id,
                "event_id": record.deduplication_key,
                "plan_version": record.plan_version,
                "kind": record.kind,
                "text": text,
                "data": record.model_dump(mode="json"),
            },
        )
