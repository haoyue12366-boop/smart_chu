"""通知意图由已校验计划确定，传输层在 P5 接入。"""

from datetime import datetime

from sqlalchemy import Connection, bindparam, insert, select, update

from app.domain.events import ExecutionPayload, RuntimeEvent
from app.domain.ports import Notification
from app.domain.runtime_clock import clock_offset
from app.domain.runtime_session import NotificationRecord
from app.domain.runtime_snapshot import RuntimeSnapshot
from app.domain.schedule import PublishedPlan
from app.domain.scheduling_problem import SchedulingProblem
from app.runtime.notification_projection import notification_records
from app.storage import models
from app.storage.repositories import RuntimeRepository


class NotificationService:
    def __init__(self, problem: SchedulingProblem | None = None) -> None:
        self.problem = problem

    def prepare(self, plan: PublishedPlan, runtime: RuntimeSnapshot) -> tuple[Notification, ...]:
        return tuple(
            Notification.model_validate(
                record.model_dump(exclude={"status", "kind", "task_ids", "sent_at"})
            )
            for record in notification_records(self.problem, plan, runtime)
        )

    def persist(self, tx: Connection, plan: PublishedPlan, runtime: RuntimeSnapshot) -> None:
        repo = RuntimeRepository(tx)
        cancellations = []
        for old in repo.notification_records(plan.session_id.root):
            if old.status == "PENDING":
                changed = old.model_copy(update={"status": "CANCELLED"})
                cancellations.append(
                    {
                        "notice_identity": old.notification_id,
                        "notice_status": "CANCELLED",
                        "notice_body": changed.model_dump_json(),
                    }
                )
        if cancellations:
            tx.execute(
                update(models.notifications)
                .where(models.notifications.c.notification_id == bindparam("notice_identity"))
                .values(status=bindparam("notice_status"), body=bindparam("notice_body")),
                cancellations,
            )
        records = [
            {
                "notification_id": record.notification_id,
                "session_id": record.session_id,
                "plan_version": record.plan_version,
                "deduplication_key": record.deduplication_key,
                "status": record.status,
                "body": record.model_dump_json(),
            }
            for record in notification_records(self.problem, plan, runtime)
        ]
        if records:
            tx.execute(insert(models.notifications), records)

    @staticmethod
    def _save(tx: Connection, record: NotificationRecord) -> None:
        tx.execute(
            update(models.notifications)
            .where(models.notifications.c.notification_id == record.notification_id)
            .values(status=record.status, body=record.model_dump_json())
        )

    def observe(
        self,
        tx: Connection,
        event: RuntimeEvent,
        runtime: RuntimeSnapshot,
        *,
        dispatch_blocked: bool,
    ) -> None:
        record = next(
            (
                e
                for e in runtime.executions
                if isinstance(event.payload, ExecutionPayload)
                and e.execution_id == event.payload.execution_id
            ),
            None,
        )
        completed = (
            set(
                record.task_ids
                if record.status in {"FAILED", "CANCELLED"}
                else record.completed_task_ids
            )
            if record
            else set()
        )
        started = set(record.started_task_ids) if record else set()
        if record is not None and event.event_type == "OPERATION_COMPLETED":
            from app.runtime.completion_notifications import record_completion

            record_completion(tx, event, runtime, record)
        for notice in RuntimeRepository(tx).notification_records(runtime.session_id.root):
            if notice.status != "PENDING":
                continue
            tasks = set(notice.task_ids)
            if (
                (tasks and tasks <= completed)
                or (notice.kind == "START" and (dispatch_blocked or tasks <= started))
                or event.event_type == "RESET_SESSION"
            ):
                self._save(tx, notice.model_copy(update={"status": "CANCELLED"}))

    def due(self, tx: Connection, session_id: str, at: datetime) -> tuple[NotificationRecord, ...]:
        session = RuntimeRepository(tx).get(session_id)
        due_at = (
            session.runtime.time_origin.at(clock_offset(session, at))
            if session.schedule_clock is not None
            else at
        )
        return tuple(
            sorted(
                (
                    r
                    for r in RuntimeRepository(tx).notification_records(session_id)
                    if r.status == "PENDING"
                    and r.trigger_at <= due_at
                    and not (session.dispatch_blocked and r.kind == "START")
                ),
                key=lambda r: (r.trigger_at, r.notification_id),
            )
        )

    def mark_sent(self, tx: Connection, notification_id: str, at: datetime) -> NotificationRecord:
        body = tx.execute(
            select(models.notifications.c.body).where(
                models.notifications.c.notification_id == notification_id
            )
        ).scalar_one()
        record = NotificationRecord.model_validate_json(body)
        if record.status == "CANCELLED":
            raise ValueError("已撤销的通知不能标记为发送")
        if record.status == "SENT":
            return record
        session = RuntimeRepository(tx).get(record.session_id)
        due_at = (
            session.runtime.time_origin.at(clock_offset(session, at))
            if session.schedule_clock is not None
            else at
        )
        if due_at < record.trigger_at:
            raise ValueError("提醒时间尚未到达")
        if record.kind == "START" and session.dispatch_blocked:
            raise ValueError("当前计划派发已暂停")
        changed = record.model_copy(update={"status": "SENT", "sent_at": at})
        self._save(tx, changed)
        return changed
