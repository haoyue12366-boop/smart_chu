"""持久消息游标；去重记录与调用方事务共同提交。"""

import json

from sqlalchemy import Connection, insert, select

from app.storage import models


def append_message(
    tx: Connection, session_id: str, identity: str, payload: dict[str, object]
) -> None:
    if (
        tx.execute(
            select(models.notification_stream.c.cursor).where(
                models.notification_stream.c.deduplication_key == identity
            )
        ).first()
        is None
    ):
        tx.execute(
            insert(models.notification_stream).values(
                session_id=session_id,
                deduplication_key=identity,
                body=json.dumps(payload, ensure_ascii=False),
            )
        )
