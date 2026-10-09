"""反馈草稿不提交事实；状态变更统一走 /events。"""

from fastapi import APIRouter, Request

from app.api.dependencies import container
from app.services.manual_feedback import prepare_feedback

router = APIRouter(prefix="/api/v1/sessions", tags=["execution"])


@router.get("/{session_id}/operations/{task_id}/feedback")
def prepare(
    session_id: str, task_id: str, request: Request, completed: bool = False
) -> dict[str, object]:
    return prepare_feedback(container(request), session_id, task_id, completed=completed)
