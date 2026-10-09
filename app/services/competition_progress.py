"""比赛任务的执行进度与显式重排，共用内部事件和持久请求身份。"""

from uuid import uuid4

from app.domain.candidates import stable_id
from app.domain.events import ReplanPayload, RuntimeEvent
from app.domain.http_time import business_time
from app.domain.ports import Deadline
from app.domain.runtime_clock import clock_offset
from app.domain.runtime_planning import RuntimePlanningResult
from app.services.container import ServiceContainer
from app.services.http_requests import admit_event, find_http_request, request_digest
from app.services.preparation_budget import PreparationBudget
from app.services.request_execution import execute_request
from app.storage.competition_tasks import HttpRequestRepository


def task_session_id(services: ServiceContainer, task_id: str) -> str:
    with services.store.engine.connect() as tx:
        sid = HttpRequestRepository(tx).task_session(task_id)
    if sid is None:
        raise KeyError("比赛任务不存在")
    return sid


def request_replan(
    services: ServiceContainer,
    task_id: str,
    key: str | None,
    started_ns: int,
    reason: str = "请求重排剩余操作",
) -> RuntimePlanningResult:
    sid = task_session_id(services, task_id)
    identity = stable_id("competition-replan", task_id, key or str(uuid4()))
    payload = {"task_id": task_id, "event_type": "REPLAN_REQUESTED", "reason": reason}
    return _request_replan(services, sid, identity, payload, started_ns, reason)


def request_session_replan(
    services: ServiceContainer,
    sid: str,
    key: str | None,
    started_ns: int,
    reason: str = "请求重排剩余操作",
) -> RuntimePlanningResult:
    """纯重排命令由服务端同步时钟后读取版本；不改写人工事实反馈的版本。"""
    identity = stable_id("session-replan", sid, key or str(uuid4()))
    payload = {"session_id": sid, "event_type": "REPLAN_REQUESTED", "reason": reason}
    return _request_replan(services, sid, identity, payload, started_ns, reason)


def _request_replan(
    services: ServiceContainer,
    sid: str,
    identity: str,
    payload: dict[str, str],
    started_ns: int,
    reason: str,
) -> RuntimePlanningResult:
    digest = request_digest(payload)
    preparation_budget = PreparationBudget(services.clock.monotonic_ns, started_ns)
    record = find_http_request(services, identity, digest)
    if record is None:
        with preparation_budget.measure(services.policy.replan_budget.total_ms) as clock_limit:
            session = services.advance_clock(sid, deadline=clock_limit)
        runtime, _ = services.for_session(sid)
        now = runtime.clock.now()
        mode = session.runtime.execution_mode
        occurred = (
            session.runtime.time_origin.at(clock_offset(session, now))
            if mode == "SCHEDULE_CLOCK"
            else session.runtime.time_origin.at(session.runtime.now_offset_sec)
            if mode == "SIMULATED"
            else business_time(session.runtime.time_origin, now)
        )
        event = RuntimeEvent(
            event_id=stable_id("http-event", identity),
            session_id=sid,
            event_type="REPLAN_REQUESTED",
            expected_state_revision=session.runtime.state_revision,
            base_plan_version=session.runtime.current_plan_version,
            occurred_at=occurred,
            received_at=now,
            source=mode,
            payload=ReplanPayload(reason=reason),
        )
        record = admit_event(services, identity, digest, event, payload)
    limit = Deadline(expires_at_ns=started_ns + services.policy.replan_budget.total_ms * 1_000_000)
    return execute_request(services, record, limit, preparation_budget=preparation_budget)
