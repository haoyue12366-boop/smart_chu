"""库存需求满足独立持久化，关联原发布并保留被替换的历史。"""

import sqlalchemy as sa
from alembic import op

revision = "0004_inventory_fulfillments"
down_revision = "0003_future_allocations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inventory_fulfillments",
        sa.Column("fulfillment_id", sa.String(), primary_key=True),
        sa.Column(
            "session_id", sa.String(), sa.ForeignKey("cooking_sessions.session_id"), nullable=False
        ),
        sa.Column(
            "publication_id",
            sa.String(),
            sa.ForeignKey("plan_versions.publication_id"),
            nullable=False,
        ),
        sa.Column("body", sa.Text(), nullable=False),
    )
    op.create_index("ix_inventory_session", "inventory_fulfillments", ["session_id"])


def downgrade() -> None:
    op.drop_table("inventory_fulfillments")
