"""可追踪的错误边界；内部异常细节只进入服务日志。"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError

from app.domain.errors import ServiceError

logger = logging.getLogger(__name__)


class ApiError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.code, self.message, self.status_code = code, message, status_code


def error_response(request: Request, code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "error": {
                "code": code,
                "message": message,
                "request_id": getattr(request.state, "request_id", "unassigned"),
            }
        },
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ServiceError)
    async def service_error(request: Request, exc: ServiceError) -> JSONResponse:
        status = (
            409
            if exc.code
            in {"STATE_CONFLICT", "IDEMPOTENCY_CONFLICT", "DUPLICATE_RECIPE", "TASK_ENDED"}
            else 503
            if exc.code
            in {"PLANNING_PENDING", "NO_FEASIBLE_PLAN", "SERVICE_NOT_READY", "STATE_INCOMPLETE"}
            else 404
            if exc.code in {"UNKNOWN_RECIPE", "UNKNOWN_TASK"}
            else 422
        )
        return error_response(request, exc.code, exc.message, status)

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError) -> JSONResponse:
        return error_response(request, exc.code, exc.message, exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        locations = [".".join(map(str, item["loc"])) for item in exc.errors()]
        return error_response(
            request, "INVALID_REQUEST", "请求字段不合法：" + "、".join(locations), 422
        )

    @app.exception_handler(KeyError)
    async def missing(request: Request, exc: KeyError) -> JSONResponse:
        return error_response(request, "NOT_FOUND", "会话或记录不存在", 404)

    @app.exception_handler(OperationalError)
    async def database_error(request: Request, exc: OperationalError) -> JSONResponse:
        logger.error("状态库不可用 request_id=%s", request.state.request_id)
        return error_response(request, "SERVICE_NOT_READY", "状态库暂不可用", 503)

    @app.exception_handler(TimeoutError)
    async def timeout(request: Request, exc: TimeoutError) -> JSONResponse:
        return error_response(
            request, "PLANNING_TIMEOUT", "本次处理预算已用尽，请查询请求结果", 503
        )

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("服务异常 request_id=%s", request.state.request_id)
        return error_response(request, "INTERNAL_ERROR", "服务处理失败，请凭请求编号定位", 500)
