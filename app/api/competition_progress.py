"""比赛接口的重排与可续读完成通知，不改变排程响应的五字段结构。"""

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import Response, StreamingResponse
from starlette.concurrency import run_in_threadpool

from app.api import notifications
from app.api.contracts import ReplanRequest
from app.api.dependencies import container
from app.services.competition_progress import request_replan, task_session_id

router = APIRouter(prefix="/api/competition", tags=["competition-progress"])


@router.post("/replan")
async def replan(
    body: ReplanRequest,
    request: Request,
    task_id: str = Query(min_length=1, max_length=128),
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key", min_length=1, max_length=200
    ),
) -> Response:
    result = await run_in_threadpool(
        request_replan,
        container(request),
        task_id,
        idempotency_key,
        request.state.started_ns,
        body.reason,
    )
    code = 202 if result.status == "PENDING" else 409 if result.status == "EVENT_REJECTED" else 200
    return Response(
        status_code=code, content=result.model_dump_json(), media_type="application/json"
    )


@router.get("/notifications")
def read(
    request: Request,
    task_id: str = Query(min_length=1, max_length=128),
    after: int = Query(default=0, ge=0),
) -> dict[str, object]:
    sid = task_session_id(container(request), task_id)
    return notifications.read(sid, request, after)


@router.get("/notifications/stream")
async def stream(
    request: Request,
    task_id: str = Query(min_length=1, max_length=128),
    after: int = Query(default=0, ge=0),
    last_event_id: int | None = Header(default=None, alias="Last-Event-ID", ge=0),
    once: bool = False,
) -> StreamingResponse:
    sid = await run_in_threadpool(task_session_id, container(request), task_id)
    return await notifications.stream(sid, request, after, last_event_id, once)
