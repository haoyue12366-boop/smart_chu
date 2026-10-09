"""仓储在调用方短事务内工作，只返回不可变领域对象。"""

from sqlalchemy import Connection, Table, insert, select, update

from app.domain.events import EventApplyResult, RuntimeEvent
from app.domain.knowledge import ReleaseRef
from app.domain.runtime_session import NotificationRecord, RuntimeSession
from app.domain.schedule import PublishedPlan
from app.domain.scheduling_problem import SchedulingProblem
from app.storage import models as m


class StateConflict(ValueError):
    pass


class RuntimeRepository:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection
        self._prefetched: dict[tuple[str, str, str], tuple[str, str] | None] = {}

    def remember_release(self, release: ReleaseRef) -> None:
        old = self.release(release.release_id)
        if old is not None:
            if old != release:
                raise StateConflict("固定知识发布身份不可改写")
            return
        self.connection.execute(
            insert(m.knowledge).values(
                release_id=release.release_id, body=release.model_dump_json()
            )
        )

    def release(self, release_id: str) -> ReleaseRef | None:
        body = self.connection.execute(
            select(m.knowledge.c.body).where(m.knowledge.c.release_id == release_id)
        ).scalar_one_or_none()
        return None if body is None else ReleaseRef.model_validate_json(body)

    def get(self, session_id: str) -> RuntimeSession:
        body = self.connection.execute(
            select(m.sessions.c.body).where(m.sessions.c.session_id == session_id)
        ).scalar_one_or_none()
        if body is None:
            raise KeyError("会话不存在：" + session_id)
        return RuntimeSession.model_validate_json(body)

    def save(
        self,
        value: RuntimeSession,
        *,
        expected_revision: int | None = None,
        expected_plan: int | None = None,
    ) -> None:
        sid = value.runtime.session_id.root
        values = dict(
            state_revision=value.runtime.state_revision,
            plan_version=value.runtime.current_plan_version,
            body=value.model_dump_json(),
        )
        if expected_revision is None:
            self.connection.execute(insert(m.sessions).values(session_id=sid, **values))
        else:
            statement = update(m.sessions).where(
                m.sessions.c.session_id == sid, m.sessions.c.state_revision == expected_revision
            )
            if expected_plan is not None:
                statement = statement.where(m.sessions.c.plan_version == expected_plan)
            if self.connection.execute(statement.values(**values)).rowcount != 1:
                raise StateConflict("会话状态或计划已变化")
        self._prefetch_save_rows(value)
        for item in value.menu:
            self._upsert(
                m.recipes,
                "recipe_instance_id",
                item.recipe_instance_id.root,
                sid,
                item.model_dump_json(),
            )
        for execution in value.runtime.executions:
            self._upsert(
                m.executions,
                "execution_id",
                execution.execution_id.root,
                sid,
                execution.model_dump_json(),
            )
        for device in value.runtime.device_states:
            self._upsert(
                m.devices,
                "state_id",
                sid + ":" + device.device_instance_id,
                sid,
                device.model_dump_json(),
            )
        if value.runtime.details:
            for fulfillment in value.runtime.details.inventory_fulfillments:
                self._upsert(
                    m.inventory_fulfillments,
                    "fulfillment_id",
                    fulfillment.fulfillment_id,
                    sid,
                    fulfillment.model_dump_json(),
                    {"publication_id": fulfillment.publication_id},
                )
            for lot in value.runtime.details.lots:
                self._upsert(m.lots, "lot_id", lot.lot_id, sid, lot.model_dump_json())
            for occupancy in value.runtime.details.occupancies:
                self._upsert(
                    m.occupancies,
                    "occupancy_id",
                    occupancy.occupancy_id,
                    sid,
                    occupancy.model_dump_json(),
                    {"execution_id": occupancy.execution_id.root},
                )
        for entry in value.ledger:
            self._upsert(
                m.ledger,
                "entry_id",
                entry.entry_id,
                sid,
                entry.model_dump_json(),
                {"event_id": entry.event_id, "lot_id": entry.lot_id},
                immutable=True,
            )
        for allocation in value.allocations:
            self._upsert(
                m.allocations,
                "allocation_id",
                allocation.allocation_id,
                sid,
                allocation.model_dump_json(),
                {"lot_id": allocation.lot_id},
            )
        for reservation in value.future_allocations:
            self._upsert(
                m.future_allocations,
                "reservation_id",
                reservation.reservation_id,
                sid,
                reservation.model_dump_json(),
            )

    def _prefetch_save_rows(self, value: RuntimeSession) -> None:
        """同一事务按身份批量读取，不漏掉其他会话拥有的冲突身份。"""
        self._prefetched.clear()
        sid, details = value.runtime.session_id.root, value.runtime.details
        groups: tuple[tuple[Table, str, tuple[str, ...]], ...] = (
            (m.recipes, "recipe_instance_id", tuple(r.recipe_instance_id.root for r in value.menu)),
            (
                m.executions,
                "execution_id",
                tuple(e.execution_id.root for e in value.runtime.executions),
            ),
            (
                m.devices,
                "state_id",
                tuple(sid + ":" + d.device_instance_id for d in value.runtime.device_states),
            ),
            (
                m.inventory_fulfillments,
                "fulfillment_id",
                tuple(f.fulfillment_id for f in details.inventory_fulfillments) if details else (),
            ),
            (m.lots, "lot_id", tuple(lot.lot_id for lot in details.lots) if details else ()),
            (
                m.occupancies,
                "occupancy_id",
                tuple(o.occupancy_id for o in details.occupancies) if details else (),
            ),
            (m.ledger, "entry_id", tuple(e.entry_id for e in value.ledger)),
            (m.allocations, "allocation_id", tuple(a.allocation_id for a in value.allocations)),
            (
                m.future_allocations,
                "reservation_id",
                tuple(r.reservation_id for r in value.future_allocations),
            ),
        )
        for table, key, identities in groups:
            for start in range(0, len(identities), 400):
                batch = identities[start : start + 400]
                for identity in batch:
                    self._prefetched[table.name, key, identity] = None
                rows = self.connection.execute(
                    select(table.c[key], table.c.body, table.c.session_id).where(
                        table.c[key].in_(batch)
                    )
                )
                for row in rows:
                    self._prefetched[table.name, key, str(row[0])] = str(row[1]), str(row[2])

    def _upsert(
        self,
        table: Table,
        key: str,
        identity: str,
        session_id: str,
        body: str,
        extra: dict[str, str] | None = None,
        *,
        immutable: bool = False,
    ) -> None:
        column = table.c[key]
        lookup = table.name, key, identity
        if lookup in self._prefetched:
            previous = self._prefetched.pop(lookup)
        else:
            row = self.connection.execute(
                select(table.c.body, table.c.session_id).where(column == identity)
            ).first()
            previous = None if row is None else (str(row.body), str(row.session_id))
        if previous is None:
            self.connection.execute(
                insert(table).values(
                    **{key: identity, "session_id": session_id, "body": body, **(extra or {})}
                )
            )
        elif previous[1] != session_id or (immutable and previous[0] != body):
            raise StateConflict("持久身份不可复用或改写历史")
        elif previous[0] != body:
            self.connection.execute(update(table).where(column == identity).values(body=body))

    def event(self, event_id: str) -> tuple[str, EventApplyResult] | None:
        row = self.connection.execute(
            select(m.events.c.payload_hash, m.events.c.result).where(
                m.events.c.event_id == event_id
            )
        ).first()
        return (
            None
            if row is None
            else (row.payload_hash, EventApplyResult.model_validate_json(row.result))
        )

    def record_event(self, event: RuntimeEvent, digest: str, result: EventApplyResult) -> None:
        self.connection.execute(
            insert(m.events).values(
                event_id=event.event_id.root,
                session_id=event.session_id.root,
                payload_hash=digest,
                body=event.model_dump_json(),
                result=result.model_dump_json(),
            )
        )

    def audit(self, audit_id: str, session_id: str, body: str) -> None:
        self._upsert(m.audits, "audit_id", audit_id, session_id, body, immutable=True)

    def publication(self, publication_id: str) -> PublishedPlan | None:
        body = self.connection.execute(
            select(m.plans.c.body).where(m.plans.c.publication_id == publication_id)
        ).scalar_one_or_none()
        return None if body is None else PublishedPlan.model_validate_json(body)

    def plan(self, session_id: str, version: int) -> PublishedPlan | None:
        body = self.connection.execute(
            select(m.plans.c.body).where(
                m.plans.c.session_id == session_id, m.plans.c.version == version
            )
        ).scalar_one_or_none()
        return None if body is None else PublishedPlan.model_validate_json(body)

    def notification_records(self, session_id: str) -> tuple[NotificationRecord, ...]:
        return tuple(
            NotificationRecord.model_validate_json(body)
            for body in self.connection.execute(
                select(m.notifications.c.body).where(m.notifications.c.session_id == session_id)
            ).scalars()
        )

    def problem(self, session_id: str, version: int) -> SchedulingProblem:
        body = self.connection.execute(
            select(m.plans.c.problem).where(
                m.plans.c.session_id == session_id, m.plans.c.version == version
            )
        ).scalar_one()
        return SchedulingProblem.model_validate_json(body)
