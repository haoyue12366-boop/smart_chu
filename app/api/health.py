"""在线就绪只依赖本地知识、运行库与工作进程。"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.dependencies import container

router = APIRouter()


@router.get("/health/live")
def live() -> dict[str, str]:
    return {"status": "live"}


@router.get("/health/ready")
def ready(request: Request) -> JSONResponse:
    services = container(request)
    available = services.ready and services.worker.is_alive
    try:
        with services.store.engine.connect() as tx:
            available = available and tx.execute(text("SELECT 1")).scalar_one() == 1
    except SQLAlchemyError:
        available = False
    return JSONResponse(
        status_code=200 if available else 503,
        content={"status": "ready" if available else "not_ready"},
    )
