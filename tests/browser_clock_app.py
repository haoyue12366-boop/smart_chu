"""仅浏览器验收使用的可控墙钟；真实 API/求解器/SQLite，不修改生产路由。"""

from datetime import UTC, datetime
from uuid import uuid4

from fastapi import FastAPI
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.config import ROOT, AppSettings
from app.domain.runtime_clock import clock_offset
from app.main import create_app as production_app
from app.runtime.clock import SimulationClock
from app.runtime.feedback import FeedbackAdapter
from app.storage import models


class ClockAdvance(BaseModel):
    seconds: int = Field(ge=0)


def create_app() -> FastAPI:
    settings = AppSettings.from_environment()
    if not settings.database_path.resolve().is_relative_to((ROOT / ".tmp").resolve()):
        raise ValueError("浏览器时钟夹具只允许独立.tmp数据库")
    app = production_app(settings)
    clock = SimulationClock(datetime.now(UTC).replace(microsecond=0))
    app.state.container.clock = clock

    @app.post("/__test/clock/advance")
    def advance(body: ClockAdvance) -> dict[str, int]:
        clock.advance(clock.offset_sec + body.seconds)
        return {"offset_sec": clock.offset_sec}

    # 生产应用最后挂载了静态站点；测试控制路由必须排在该兜底挂载之前。
    app.router.routes.insert(0, app.router.routes.pop())

    @app.post("/__test/sessions/end")
    def end_previous_sessions() -> dict[str, int]:
        services = app.state.container
        with services.store.engine.connect() as tx:
            session_ids = tuple(tx.execute(select(models.sessions.c.session_id)).scalars())
        ended = 0
        for sid in session_ids:
            runtime, _ = services.for_session(sid)
            current = runtime.get(sid)
            if current.status != "ACTIVE":
                continue
            at = current.runtime.time_origin.at(
                max(current.runtime.now_offset_sec, clock_offset(current, runtime.clock.now()))
            )
            reset = FeedbackAdapter().event(
                current,
                str(uuid4()),
                "RESET_SESSION",
                {"reason": "独立浏览器用例结束"},
                at,
            )
            result = runtime.apply_event(reset)
            if result.status != "APPLIED":
                raise ValueError("测试会话隔离失败：" + str(result.rejection_reason))
            ended += 1
        return {"ended": ended}

    app.router.routes.insert(0, app.router.routes.pop())
    return app
