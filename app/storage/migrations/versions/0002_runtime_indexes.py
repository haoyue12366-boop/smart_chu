"""按会话读取历史和待发通知的索引；保留所有既有事实。"""

from alembic import op

revision = "0002_runtime_indexes"
down_revision = "0001_runtime"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_runtime_events_session", "runtime_events", ["session_id"])
    op.create_index("ix_notifications_pending", "notifications", ["session_id", "status"])
    op.create_index("ix_executions_session", "operation_executions", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_executions_session", table_name="operation_executions")
    op.drop_index("ix_notifications_pending", table_name="notifications")
    op.drop_index("ix_runtime_events_session", table_name="runtime_events")
