"""保存原始客户端请求；接收时刻和业务秒级投影分别追溯。"""

import sqlalchemy as sa
from alembic import op

revision = "0007_http_request_archive"
down_revision = "0006_notification_stream"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("http_requests", sa.Column("client_body", sa.Text()))


def downgrade() -> None:
    op.drop_column("http_requests", "client_body")
