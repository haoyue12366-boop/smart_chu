"""通知按持久游标读取，SSE 可重复投递，客户端按身份去重。"""

import asyncio
import sqlite3
from collections.abc import AsyncIterator

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.exc import OperationalError
from starlette.concurrency import run_in_threadpool

from app.api.dependencies import container
from app.services.notification_dispatcher import NotificationDispatcher

router = APIRouter(prefix="/api/v1/sessions", tags=["notifications"])


def sqlite_contention(error: OperationalError) -> bool:
    """只识别 SQLite 的可恢复锁竞争，不掩盖损坏、I/O 或其他驱动错误。"""
    original = error.orig
    return isinstance(original, sqlite3.OperationalError) and getattr(
        original, "sqlite_errorcode", 0
    ) & 0xFF in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}


@router.get("/{session_id}/notifications")
def read(
    session_id: str, request: Request, after: int = Query(default=0, ge=0)
) -> dict[str, object]:
    messages = NotificationDispatcher(container(request)).poll(session_id, after)
    return {
        "messages": [m.model_dump(mode="json") for m in messages],
        "cursor": messages[-1].cursor if messages else after,
    }


@router.get("/{session_id}/notifications/stream")
async def stream(
    session_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
    last_event_id: int | None = Header(default=None, alias="Last-Event-ID", ge=0),
    once: bool = False,
) -> StreamingResponse:
    dispatcher = NotificationDispatcher(container(request))
    # 在响应开始前确认会话和首批消息，错误仍可返回正常 JSON。
    cursor = max(after, last_event_id or 0)
    initial = await run_in_threadpool(dispatcher.poll, session_id, cursor)

    async def frames() -> AsyncIterator[str]:
        nonlocal cursor
        batch = initial
        while not await request.is_disconnected():
            for message in batch:
                cursor = message.cursor
                yield f"id: {cursor}\nevent: notification\ndata: {message.model_dump_json()}\n\n"
            if once:
                return
            yield ": keepalive\n\n"
            await asyncio.sleep(0.5)
            # 响应已经开始，不能再转为 JSON 503。事务会回滚；不推进游标，
            # 下一周期从同一位置重试。空批次避免重发上一轮已经输出的消息。
            batch = ()
            try:
                batch = await run_in_threadpool(dispatcher.poll, session_id, cursor)
            except OperationalError as error:
                if not sqlite_contention(error):
                    raise

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
