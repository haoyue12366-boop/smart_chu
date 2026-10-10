"""FastAPI 应用工厂；生命周期之外没有连接、工作进程或求解副作用。"""

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response
from starlette.staticfiles import StaticFiles

from app.api import (
    competition,
    competition_progress,
    competition_tasks,
    events,
    feedback,
    health,
    language_events,
    notifications,
    plans,
    recipes,
    sessions,
)
from app.api.errors import install_error_handlers
from app.config import AppSettings
from app.services.container import ServiceContainer
from app.storage.decoded_models import decoded_models_scope

logger = logging.getLogger(__name__)


def create_app(settings: AppSettings | None = None) -> FastAPI:
    settings = settings or AppSettings.from_environment()
    services = ServiceContainer(settings)

    def recover_with_decoded_models() -> None:
        with decoded_models_scope():
            services.recover_pending()

    async def recovery_loop(stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=settings.recovery_interval_sec)
                break
            except TimeoutError:
                pass
            try:
                await run_in_threadpool(recover_with_decoded_models)
            except Exception:
                logger.exception("持久待重排作业恢复失败")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        recovery: asyncio.Task[None] | None = None
        stop = asyncio.Event()
        try:
            await run_in_threadpool(services.start)
            recovery = asyncio.create_task(recovery_loop(stop))
            yield
        finally:
            if recovery is not None:
                services.ready = False
                services.stopping.set()
                stop.set()
                # 取消异步包装不会终止正在执行的线程；等待当前有期限的
                # 恢复作业结束，再关闭它仍在使用的工作进程与运行库。
                await recovery
            await run_in_threadpool(services.close)

    app = FastAPI(title="智能烹饪调度 API", version="p5-v1", lifespan=lifespan)
    app.state.container = services
    install_error_handlers(app)

    @app.middleware("http")
    async def trace(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request.state.started_ns = time.monotonic_ns()
        request.state.request_id = str(uuid4())
        # 一个 API 实例的事件循环持有前台计数；后台恢复只读取它。
        # 从准入之前到回执构造完成，恢复器不抢占这份前台请求。
        mutation = request.method not in {"GET", "HEAD", "OPTIONS"}
        if mutation:
            services.foreground_requests += 1
        try:
            with decoded_models_scope():
                response = await call_next(request)
        finally:
            if mutation:
                services.foreground_requests -= 1
        elapsed = (time.monotonic_ns() - request.state.started_ns) / 1_000_000
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["Server-Timing"] = f"application;dur={elapsed:.3f}"
        return response

    for router in (
        health.router,
        sessions.router,
        events.router,
        feedback.router,
        plans.router,
        recipes.router,
        competition.router,
        competition_progress.router,
        competition_tasks.router,
        notifications.router,
        language_events.router,
    ):
        app.include_router(router)
    if settings.frontend_path.is_dir():
        app.mount("/", StaticFiles(directory=settings.frontend_path, html=True), name="workbench")
    return app
