"""模拟控制阶段与固定目标共同持久化，支持提交后中断恢复。"""

import sqlalchemy as sa
from alembic import op

revision = "0008_simulation_control"
down_revision = "0007_http_request_archive"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("http_requests", sa.Column("control_phase", sa.String()))


def downgrade() -> None:
    op.drop_column("http_requests", "control_phase")
