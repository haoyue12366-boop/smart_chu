"""语言路由只接收文本契约并委托服务层。"""

from fastapi import APIRouter, Request

from app.api.dependencies import container
from app.domain.intent import LanguageRequest
from app.services.language_events import process_language_request

router = APIRouter(prefix="/api/v1/sessions", tags=["language"])


@router.post("/{session_id}/language-events")
async def interpret(session_id: str, body: LanguageRequest, request: Request) -> dict[str, object]:
    return await process_language_request(
        container(request), session_id, body, request.state.started_ns
    )
