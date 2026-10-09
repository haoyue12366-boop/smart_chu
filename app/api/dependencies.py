"""请求上下文保留从 HTTP 入口开始的共享截止时间。"""

from fastapi import Request

from app.domain.ports import Deadline
from app.services.container import ServiceContainer


def container(request: Request) -> ServiceContainer:
    value: ServiceContainer = request.app.state.container
    return value


def deadline(request: Request, budget_ms: int) -> Deadline:
    return Deadline(expires_at_ns=request.state.started_ns + budget_ms * 1_000_000)
