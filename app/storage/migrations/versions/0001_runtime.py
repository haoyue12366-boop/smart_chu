"""P4 初始运行库。"""

from alembic import op
from sqlalchemy import Column, ForeignKey, Integer, String, Text, UniqueConstraint

revision = "0001_runtime"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 本版本固定表定义，不导入随未来开发变化的 ORM metadata。
    op.create_table(
        "cooking_sessions",
        Column("session_id", String, primary_key=True),
        Column("state_revision", Integer, nullable=False),
        Column("plan_version", Integer, nullable=False),
        Column("body", Text, nullable=False),
    )
    op.create_table(
        "runtime_events",
        Column("event_id", String, primary_key=True),
        Column("session_id", String, ForeignKey("cooking_sessions.session_id"), nullable=False),
        Column("payload_hash", String, nullable=False),
        Column("body", Text, nullable=False),
        Column("result", Text, nullable=False),
    )
    op.create_table(
        "plan_versions",
        Column("publication_id", String, primary_key=True),
        Column("session_id", String, ForeignKey("cooking_sessions.session_id"), nullable=False),
        Column("version", Integer, nullable=False),
        Column("body", Text, nullable=False),
        Column("problem", Text, nullable=False),
        UniqueConstraint("session_id", "version"),
    )
    op.create_table(
        "notifications",
        Column("notification_id", String, primary_key=True),
        Column("session_id", String, ForeignKey("cooking_sessions.session_id"), nullable=False),
        Column("plan_version", Integer, nullable=False),
        Column("deduplication_key", String, nullable=False, unique=True),
        Column("status", String, nullable=False),
        Column("body", Text, nullable=False),
    )
    op.create_table(
        "audit_records",
        Column("audit_id", String, primary_key=True),
        Column("session_id", String, ForeignKey("cooking_sessions.session_id")),
        Column("body", Text, nullable=False),
    )
    for name, key in (
        ("operation_executions", "execution_id"),
        ("material_lots", "lot_id"),
        ("recipe_instances", "recipe_instance_id"),
        ("device_runtime_states", "state_id"),
        ("recovery_records", "recovery_id"),
        ("diagnostic_reports", "diagnostic_id"),
        ("plan_task_mapping", "mapping_id"),
    ):
        op.create_table(
            name,
            Column(key, String, primary_key=True),
            Column("session_id", String, ForeignKey("cooking_sessions.session_id"), nullable=False),
            Column("body", Text, nullable=False),
        )
    op.create_table(
        "resource_occupancies",
        Column("occupancy_id", String, primary_key=True),
        Column("session_id", String, ForeignKey("cooking_sessions.session_id"), nullable=False),
        Column(
            "execution_id", String, ForeignKey("operation_executions.execution_id"), nullable=False
        ),
        Column("body", Text, nullable=False),
    )
    op.create_table(
        "material_adjustments",
        Column("entry_id", String, primary_key=True),
        Column("session_id", String, ForeignKey("cooking_sessions.session_id"), nullable=False),
        Column("lot_id", String, ForeignKey("material_lots.lot_id"), nullable=False),
        Column("event_id", String, ForeignKey("runtime_events.event_id"), nullable=False),
        Column("body", Text, nullable=False),
    )
    op.create_table(
        "material_allocations",
        Column("allocation_id", String, primary_key=True),
        Column("session_id", String, ForeignKey("cooking_sessions.session_id"), nullable=False),
        Column("lot_id", String, ForeignKey("material_lots.lot_id"), nullable=False),
        Column("body", Text, nullable=False),
    )
    op.create_table(
        "knowledge_releases",
        Column("release_id", String, primary_key=True),
        Column("body", Text, nullable=False),
    )


def downgrade() -> None:
    raise RuntimeError("运行事实不可通过删库降级；请恢复有审计的数据库备份")
