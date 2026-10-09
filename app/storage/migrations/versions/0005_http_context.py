"""P5 任务关联与稳定 HTTP 请求身份；不改写 P4 历史表。"""

import sqlalchemy as sa
from alembic import op

revision = "0005_http_context"
down_revision = "0004_inventory_fulfillments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "competition_tasks",
        sa.Column("task_id", sa.String(), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(),
            sa.ForeignKey("cooking_sessions.session_id"),
            nullable=False,
            unique=True,
        ),
    )
    op.create_table(
        "http_requests",
        sa.Column("request_id", sa.String(), primary_key=True),
        sa.Column("payload_hash", sa.String(), nullable=False),
        sa.Column(
            "session_id", sa.String(), sa.ForeignKey("cooking_sessions.session_id"), nullable=False
        ),
        sa.Column("event_body", sa.Text(), nullable=False),
        sa.Column("result_body", sa.Text()),
        sa.Column("response_body", sa.Text()),
        sa.Column("publication_id", sa.String(), sa.ForeignKey("plan_versions.publication_id")),
    )
    op.create_index("ix_http_requests_session", "http_requests", ["session_id"])


def downgrade() -> None:
    op.drop_table("http_requests")
    op.drop_table("competition_tasks")
