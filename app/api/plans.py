"""历史计划只读查询及精确秒级问题，不触发求解。"""

from fastapi import APIRouter, Request

from app.api.dependencies import container
from app.services.plan_queries import read_plan

router = APIRouter(prefix="/api/v1/sessions", tags=["plans"])


@router.get("/{session_id}/plans/{version}")
def read(session_id: str, version: int, request: Request) -> dict[str, object]:
    return read_plan(container(request), session_id, version)
