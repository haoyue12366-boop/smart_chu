"""有效事件先落事实；失败或等待的重排明确返回。"""

from fastapi import APIRouter, Request
from fastapi.responses import Response
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from app.api.contracts import EventRequest
from app.api.dependencies import container, deadline
from app.api.errors import ApiError
from app.domain.candidates import stable_id
from app.domain.events import RuntimeEvent, SimulationPayload
from app.domain.http_time import business_time
from app.domain.runtime_clock import clock_offset
from app.services.http_requests import admit_event, find_http_request, request_digest
from app.services.preparation_budget import PreparationBudget
from app.services.request_execution import execute_request

router = APIRouter(prefix="/api/v1/sessions", tags=["events"])


@router.post("/{session_id}/events")
async def apply(session_id: str, body: EventRequest, request: Request) -> Response:
    services = container(request)
    runtime, _ = await run_in_threadpool(services.for_session, session_id)
    current = await run_in_threadpool(runtime.get, session_id)
    preparation_budget = PreparationBudget(services.clock.monotonic_ns, request.state.started_ns)
    identity = stable_id("internal-event", body.event_id)
    digest = request_digest({"session_id": session_id, **body.model_dump(mode="json")})
    record = await run_in_threadpool(find_http_request, services, identity, digest)
    if record is None:
        with preparation_budget.measure(current.policy.replan_budget.total_ms) as clock_limit:
            current = await run_in_threadpool(
                services.advance_clock, session_id, deadline=clock_limit
            )
        now = runtime.clock.now()
        occurred = (
            current.runtime.time_origin.at(clock_offset(current, now))
            if current.runtime.execution_mode == "SCHEDULE_CLOCK"
            else now
        )
        try:
            if body.event_type == "ADVANCE_SIMULATION" and isinstance(
                body.payload, SimulationPayload
            ):
                now = current.runtime.time_origin.at(
                    current.runtime.now_offset_sec + body.payload.advance_sec
                )
                occurred = now
                if body.occurred_at is not None and body.occurred_at != now:
                    raise ValueError("模拟推进时间必须等于当前偏移加请求增量")
            event = RuntimeEvent(
                session_id=session_id,
                received_at=now,
                **body.model_dump(exclude={"occurred_at"}),
                occurred_at=business_time(
                    current.runtime.time_origin, body.occurred_at or occurred
                ),
            )
        except (ValidationError, ValueError) as exc:
            raise ApiError("INVALID_EVENT", "事件类型、载荷或来源不匹配") from exc
        record = await run_in_threadpool(
            admit_event, services, identity, digest, event, body.model_dump(mode="json")
        )
    result = await run_in_threadpool(
        execute_request,
        services,
        record,
        deadline(request, current.policy.replan_budget.total_ms),
        preparation_budget=preparation_budget,
    )
    code = 202 if result.status == "PENDING" else 409 if result.status == "EVENT_REJECTED" else 200
    return Response(
        status_code=code, content=result.model_dump_json(), media_type="application/json"
    )
