"""未来供给预约独立保存，不生成尚未发生的实物批次。"""

import sqlalchemy as sa
from alembic import op

revision = "0003_future_allocations"
down_revision = "0002_runtime_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "future_material_allocations",
        sa.Column("reservation_id", sa.String(), primary_key=True),
        sa.Column(
            "session_id", sa.String(), sa.ForeignKey("cooking_sessions.session_id"), nullable=False
        ),
        sa.Column("body", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("future_material_allocations")
