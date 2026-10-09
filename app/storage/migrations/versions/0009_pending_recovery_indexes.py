"""为后台恢复建立部分索引；不改事件、事实、计划和回执内容。"""

from alembic import op

revision = "0009_pending_recovery_indexes"
down_revision = "0008_simulation_control"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX ix_sessions_pending_recovery ON cooking_sessions(session_id) "
        "WHERE json_extract(body, '$.requires_replan') = 1 "
        "AND json_extract(body, '$.status') = 'ACTIVE'"
    )
    op.execute(
        "CREATE INDEX ix_http_pending_recovery ON http_requests(request_id) "
        "WHERE result_body IS NULL OR json_extract(result_body, '$.status') = 'PENDING'"
    )


def downgrade() -> None:
    op.drop_index("ix_http_pending_recovery", table_name="http_requests")
    op.drop_index("ix_sessions_pending_recovery", table_name="cooking_sessions")
