"""运行表及强制唯一身份；大领域对象保存为版本化 JSON。"""

from sqlalchemy import (
    Column,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)

metadata = MetaData()
sessions = Table(
    "cooking_sessions",
    metadata,
    Column("session_id", String, primary_key=True),
    Column("state_revision", Integer, nullable=False),
    Column("plan_version", Integer, nullable=False),
    Column("body", Text, nullable=False),
)
events = Table(
    "runtime_events",
    metadata,
    Column("event_id", String, primary_key=True),
    Column("session_id", ForeignKey("cooking_sessions.session_id"), nullable=False),
    Column("payload_hash", String, nullable=False),
    Column("body", Text, nullable=False),
    Column("result", Text, nullable=False),
)
plans = Table(
    "plan_versions",
    metadata,
    Column("publication_id", String, primary_key=True),
    Column("session_id", ForeignKey("cooking_sessions.session_id"), nullable=False),
    Column("version", Integer, nullable=False),
    Column("body", Text, nullable=False),
    Column("problem", Text, nullable=False),
    UniqueConstraint("session_id", "version"),
)
notifications = Table(
    "notifications",
    metadata,
    Column("notification_id", String, primary_key=True),
    Column("session_id", ForeignKey("cooking_sessions.session_id"), nullable=False),
    Column("plan_version", Integer, nullable=False),
    Column("deduplication_key", String, nullable=False, unique=True),
    Column("status", String, nullable=False),
    Column("body", Text, nullable=False),
)
audits = Table(
    "audit_records",
    metadata,
    Column("audit_id", String, primary_key=True),
    Column("session_id", ForeignKey("cooking_sessions.session_id")),
    Column("body", Text, nullable=False),
)


def projection(name: str, key: str) -> Table:
    return Table(
        name,
        metadata,
        Column(key, String, primary_key=True),
        Column("session_id", ForeignKey("cooking_sessions.session_id"), nullable=False),
        Column("body", Text, nullable=False),
    )


executions = projection("operation_executions", "execution_id")
lots = projection("material_lots", "lot_id")
occupancies = Table(
    "resource_occupancies",
    metadata,
    Column("occupancy_id", String, primary_key=True),
    Column("session_id", ForeignKey("cooking_sessions.session_id"), nullable=False),
    Column("execution_id", ForeignKey("operation_executions.execution_id"), nullable=False),
    Column("body", Text, nullable=False),
)
ledger = Table(
    "material_adjustments",
    metadata,
    Column("entry_id", String, primary_key=True),
    Column("session_id", ForeignKey("cooking_sessions.session_id"), nullable=False),
    Column("lot_id", ForeignKey("material_lots.lot_id"), nullable=False),
    Column("event_id", ForeignKey("runtime_events.event_id"), nullable=False),
    Column("body", Text, nullable=False),
)
allocations = Table(
    "material_allocations",
    metadata,
    Column("allocation_id", String, primary_key=True),
    Column("session_id", ForeignKey("cooking_sessions.session_id"), nullable=False),
    Column("lot_id", ForeignKey("material_lots.lot_id"), nullable=False),
    Column("body", Text, nullable=False),
)
recipes = projection("recipe_instances", "recipe_instance_id")
future_allocations = projection("future_material_allocations", "reservation_id")
inventory_fulfillments = Table(
    "inventory_fulfillments",
    metadata,
    Column("fulfillment_id", String, primary_key=True),
    Column("session_id", ForeignKey("cooking_sessions.session_id"), nullable=False),
    Column("publication_id", ForeignKey("plan_versions.publication_id"), nullable=False),
    Column("body", Text, nullable=False),
)
Index("ix_inventory_session", inventory_fulfillments.c.session_id)
devices = projection("device_runtime_states", "state_id")
recoveries = projection("recovery_records", "recovery_id")
diagnostics = projection("diagnostic_reports", "diagnostic_id")
task_mapping = projection("plan_task_mapping", "mapping_id")
knowledge = Table(
    "knowledge_releases",
    metadata,
    Column("release_id", String, primary_key=True),
    Column("body", Text, nullable=False),
)

Index("ix_runtime_events_session", events.c.session_id)
Index("ix_notifications_pending", notifications.c.session_id, notifications.c.status)
Index("ix_executions_session", executions.c.session_id)

competition_tasks = Table(
    "competition_tasks",
    metadata,
    Column("task_id", String, primary_key=True),
    Column("session_id", ForeignKey("cooking_sessions.session_id"), nullable=False, unique=True),
)
http_requests = Table(
    "http_requests",
    metadata,
    Column("request_id", String, primary_key=True),
    Column("payload_hash", String, nullable=False),
    Column("session_id", ForeignKey("cooking_sessions.session_id"), nullable=False),
    Column("event_body", Text, nullable=False),
    Column("client_body", Text),
    Column("control_phase", String),
    Column("result_body", Text),
    Column("response_body", Text),
    Column("publication_id", ForeignKey("plan_versions.publication_id")),
)
Index("ix_http_requests_session", http_requests.c.session_id)

notification_stream = Table(
    "notification_stream",
    metadata,
    Column("cursor", Integer, primary_key=True, autoincrement=True),
    Column("session_id", ForeignKey("cooking_sessions.session_id"), nullable=False),
    Column("deduplication_key", String, nullable=False, unique=True),
    Column("body", Text, nullable=False),
)
Index("ix_stream_session_cursor", notification_stream.c.session_id, notification_stream.c.cursor)
