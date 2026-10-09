"""持久通知游标支持断线恢复和多个观察客户端。"""

import sqlalchemy as sa
from alembic import op

revision = "0006_notification_stream"
down_revision = "0005_http_context"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notification_stream",
        sa.Column("cursor", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "session_id", sa.String(), sa.ForeignKey("cooking_sessions.session_id"), nullable=False
        ),
        sa.Column("deduplication_key", sa.String(), nullable=False, unique=True),
        sa.Column("body", sa.Text(), nullable=False),
    )
    op.create_index("ix_stream_session_cursor", "notification_stream", ["session_id", "cursor"])


def downgrade() -> None:
    op.drop_table("notification_stream")
