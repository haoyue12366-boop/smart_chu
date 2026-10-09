"""比赛直接数组路由；业务和持久化编排统一交给服务层。"""

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from app.api.competition_adapter import CompetitionAdapter
from app.api.dependencies import container
from app.domain.competition_contract import RecipeSelection
from app.services.competition import process_competition_request

router = APIRouter(prefix="/api/competition", tags=["competition"])


@router.post("/plan")
async def plan(
    body: list[RecipeSelection],
    request: Request,
    task_id: str | None = Query(default=None, min_length=1, max_length=128),
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key", min_length=1, max_length=200
    ),
) -> Response:
    services = container(request)
    result = await run_in_threadpool(
        process_competition_request,
        services,
        body,
        task_id,
        idempotency_key,
        request.state.started_ns,
        CompetitionAdapter(services.settings.timezone).to_response,
    )
    return Response(content=result, media_type="application/json")
