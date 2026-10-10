"""会话初排、当前状态和版本读取。"""

from typing import Literal

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.api.contracts import AddRecipeRequest, CreateSessionRequest, ReplanRequest
from app.api.dependencies import container, deadline
from app.domain.candidates import stable_id
from app.domain.events import EventSource
from app.services.competition_progress import request_session_replan
from app.services.http_requests import admit_menu
from app.services.preparation_budget import PreparationBudget
from app.services.request_execution import execute_request
from app.services.session_queries import read_session

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])


@router.post("")
async def create(body: CreateSessionRequest, request: Request) -> JSONResponse:
    services = container(request)
    preparation_budget = PreparationBudget(services.clock.monotonic_ns, request.state.started_ns)
    record = await run_in_threadpool(
        admit_menu,
        services,
        body.recipes,
        stable_id("internal-start", body.event_id),
        mode=EventSource(body.mode),
        digest_payload=body.model_dump(mode="json"),
        preparation_budget=preparation_budget,
    )
    result = await run_in_threadpool(
        execute_request,
        services,
        record,
        deadline(request, services.policy.initial_budget.total_ms),
        preparation_budget=preparation_budget,
    )
    return JSONResponse(
        status_code=202
        if result.status == "PENDING"
        else 409
        if result.status == "EVENT_REJECTED"
        else 200,
        content={"session_id": record.session_id, **result.model_dump(mode="json")},
    )


@router.get("/{session_id}")
def read(
    session_id: str, request: Request, view: Literal["full", "workbench"] = "full"
) -> dict[str, object]:
    return read_session(container(request), session_id, view=view)


@router.post("/{session_id}/recipes")
async def add_recipe(session_id: str, body: AddRecipeRequest, request: Request) -> JSONResponse:
    """加菜按服务端当前时钟准入，仍核对计划版本并持久绑定请求身份。"""
    services = container(request)
    runtime, _ = await run_in_threadpool(services.for_session, session_id)
    current = await run_in_threadpool(runtime.get, session_id)
    preparation_budget = PreparationBudget(services.clock.monotonic_ns, request.state.started_ns)
    record = await run_in_threadpool(
        admit_menu,
        services,
        body.recipes,
        stable_id("internal-add", session_id, body.event_id),
        session_id=session_id,
        base_plan_version=body.base_plan_version,
        digest_payload={"session_id": session_id, **body.model_dump(mode="json")},
        preparation_budget=preparation_budget,
    )
    result = await run_in_threadpool(
        execute_request,
        services,
        record,
        deadline(request, current.policy.replan_budget.total_ms),
        preparation_budget=preparation_budget,
    )
    return JSONResponse(
        status_code=202
        if result.status == "PENDING"
        else 409
        if result.status == "EVENT_REJECTED"
        else 200,
        content=result.model_dump(mode="json"),
    )


@router.post("/{session_id}/replan")
async def replan(
    session_id: str,
    body: ReplanRequest,
    request: Request,
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key", min_length=1, max_length=200
    ),
) -> JSONResponse:
    result = await run_in_threadpool(
        request_session_replan,
        container(request),
        session_id,
        idempotency_key,
        request.state.started_ns,
        body.reason,
    )
    return JSONResponse(
        status_code=202
        if result.status == "PENDING"
        else 409
        if result.status == "EVENT_REJECTED"
        else 200,
        content=result.model_dump(mode="json"),
    )
