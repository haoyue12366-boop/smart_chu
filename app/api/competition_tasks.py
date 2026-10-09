"""工作台只读关联；比赛五字段响应保持原契约。"""

from fastapi import APIRouter, Request

from app.api.dependencies import container
from app.services.plan_queries import competition_session

router = APIRouter(prefix="/api/v1/competition-tasks", tags=["competition-context"])


@router.get("/{task_id}")
def read(task_id: str, request: Request) -> dict[str, object]:
    return competition_session(container(request), task_id)
