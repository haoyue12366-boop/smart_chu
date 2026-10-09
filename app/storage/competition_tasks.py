"""任务和请求仓储在调用方短事务内工作。"""

from typing import Literal

from sqlalchemy import Connection, func, insert, literal_column, or_, select, update

from app.domain.base import FrozenModel
from app.domain.errors import ServiceError
from app.domain.events import RuntimeEvent
from app.domain.runtime_planning import RuntimePlanningResult
from app.domain.schedule import PublishedPlan
from app.storage import models


class HttpRequestRecord(FrozenModel):
    request_id: str
    payload_hash: str
    session_id: str
    event: RuntimeEvent
    client_body: str | None = None
    result: RuntimePlanningResult | None = None
    response_body: str | None = None
    publication_id: str | None = None
    control_phase: Literal["VALIDATED", "EXECUTED"] | None = None


class HttpRequestRepository:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def task_session(self, task_id: str) -> str | None:
        value: str | None = self.connection.execute(
            select(models.competition_tasks.c.session_id).where(
                models.competition_tasks.c.task_id == task_id
            )
        ).scalar_one_or_none()
        return value

    def map_task(self, task_id: str, session_id: str) -> None:
        self.connection.execute(
            insert(models.competition_tasks).values(task_id=task_id, session_id=session_id)
        )

    def get(self, identity: str, digest: str | None = None) -> HttpRequestRecord | None:
        row = self.connection.execute(
            select(models.http_requests).where(models.http_requests.c.request_id == identity)
        ).first()
        if row is None:
            return None
        if digest is not None and row.payload_hash != digest:
            raise ServiceError("IDEMPOTENCY_CONFLICT", "同一幂等身份的请求内容不同")
        return HttpRequestRecord(
            request_id=row.request_id,
            payload_hash=row.payload_hash,
            session_id=row.session_id,
            event=RuntimeEvent.model_validate_json(row.event_body),
            client_body=row.client_body,
            result=RuntimePlanningResult.model_validate_json(row.result_body)
            if row.result_body
            else None,
            response_body=row.response_body,
            publication_id=row.publication_id,
            control_phase=row.control_phase,
        )

    def reserve(self, value: HttpRequestRecord) -> None:
        self.connection.execute(
            insert(models.http_requests).values(
                request_id=value.request_id,
                payload_hash=value.payload_hash,
                session_id=value.session_id,
                event_body=value.event.model_dump_json(),
                client_body=value.client_body,
            )
        )

    def finish(
        self, identity: str, result: RuntimePlanningResult, response_body: str | None = None
    ) -> None:
        old = self.get(identity)
        if old is None:
            raise KeyError("请求尚未登记")
        if old.result is not None and old.result.status != "PENDING":
            recovered_publication = (
                old.result.status == "FAILED"
                and old.result.event is not None
                and old.result.event.status == "APPLIED"
                and old.result.event.requires_replan
                and old.response_body is None
                and result.status == "PUBLISHED"
                and result.plan is not None
                and result.plan.session_id.root == old.session_id
                and result.event == old.result.event
                and result.plan.state_revision >= old.result.event.state_revision
            )
            if not recovered_publication:
                return
        self.connection.execute(
            update(models.http_requests)
            .where(models.http_requests.c.request_id == identity)
            .values(
                result_body=result.model_dump_json(),
                response_body=response_body,
                publication_id=result.plan.publication_id if result.plan else None,
            )
        )

    def control(
        self,
        record: HttpRequestRecord,
        phase: Literal["VALIDATED", "EXECUTED"],
        event: RuntimeEvent | None = None,
    ) -> HttpRequestRecord:
        changed = record.model_copy(update={"control_phase": phase, "event": event or record.event})
        self.connection.execute(
            update(models.http_requests)
            .where(models.http_requests.c.request_id == record.request_id)
            .values(control_phase=phase, event_body=changed.event.model_dump_json())
        )
        return changed

    def bind_response(self, identity: str, plan: PublishedPlan, response: str) -> str:
        """绑定首份成功响应，调用者必须已完成独立投影校验。"""
        old = self.connection.execute(
            select(
                models.http_requests.c.session_id,
                models.http_requests.c.publication_id,
                models.http_requests.c.response_body,
                func.json_extract(models.http_requests.c.result_body, "$.status").label("status"),
            ).where(models.http_requests.c.request_id == identity)
        ).first()
        if old is None:
            raise KeyError("请求尚未登记")
        original_body: str | None = old.response_body
        if original_body is not None:
            return original_body
        if (
            old.session_id != plan.session_id.root
            or old.status != "PUBLISHED"
            or old.publication_id != plan.publication_id
        ):
            raise ServiceError("STATE_CONFLICT", "响应必须绑定该请求首次持久化的成功发布")
        self.connection.execute(
            update(models.http_requests)
            .where(models.http_requests.c.request_id == identity)
            .values(response_body=response)
        )
        return response

    def pending(self, limit: int = 50) -> tuple[HttpRequestRecord, ...]:
        identities = self.connection.execute(
            select(models.http_requests.c.request_id)
            .where(
                or_(
                    models.http_requests.c.result_body.is_(None),
                    func.json_extract(
                        models.http_requests.c.result_body, literal_column("'$.status'")
                    )
                    == literal_column("'PENDING'"),
                )
            )
            .limit(limit)
        ).scalars()
        return tuple(
            record for identity in identities if (record := self.get(identity)) is not None
        )
