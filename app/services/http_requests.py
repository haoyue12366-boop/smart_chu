"""稳定 HTTP 身份绑定事件和发布结果；服务器接收时间不参与客户端摘要。"""

import hashlib
import json
from datetime import datetime

from app.domain.candidates import stable_id
from app.domain.errors import ServiceError
from app.domain.events import EventRecipe, EventSource, MenuPayload, RuntimeEvent
from app.domain.http_time import business_time
from app.domain.runtime_clock import clock_offset
from app.domain.runtime_facts import RuntimeDetails
from app.domain.runtime_session import RuntimeSession
from app.domain.runtime_snapshot import RuntimeSnapshot
from app.domain.time import TimeOrigin
from app.services.container import ServiceContainer
from app.services.preparation_budget import PreparationBudget
from app.services.session_api import check_recipes
from app.storage.competition_tasks import HttpRequestRecord, HttpRequestRepository
from app.storage.repositories import RuntimeRepository


def request_digest(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def new_session(
    services: ServiceContainer, sid: str, mode: EventSource, at: datetime
) -> RuntimeSession:
    release = services.knowledge.release
    return RuntimeSession(
        runtime=RuntimeSnapshot(
            session_id=sid,
            state_revision=0,
            current_plan_version=0,
            knowledge_version=release.knowledge_version,
            rule_version=release.rule_version,
            snapshot_id=release.snapshot_id,
            time_origin=TimeOrigin(start_at=at),
            now_offset_sec=0,
            execution_mode=mode,
            details=RuntimeDetails(),
        ),
        policy=services.policy,
        knowledge_release_id=release.release_id,
    )


def admit_menu(
    services: ServiceContainer,
    recipes: tuple[EventRecipe, ...],
    identity: str,
    *,
    task_id: str | None = None,
    session_id: str | None = None,
    base_plan_version: int | None = None,
    mode: EventSource = EventSource.MANUAL_CONFIRM,
    digest_payload: object | None = None,
    preparation_budget: PreparationBudget | None = None,
) -> HttpRequestRecord:
    check_recipes(recipes, services)
    digest = request_digest(
        digest_payload
        if digest_payload is not None
        else {
            "recipes": [r.model_dump(mode="json") for r in recipes],
            "mode": mode.value,
        }
    )
    # 重试首先观察持久身份；新加菜事件在事务外同步时钟，再按最新版本准入。
    with services.store.engine.connect() as tx:
        requests = HttpRequestRepository(tx)
        old = requests.get(identity, digest)
        if old is not None:
            return old
        existing_sid = session_id or (
            requests.task_session(task_id) if task_id is not None else None
        )
    if existing_sid is not None:
        preparation_budget = preparation_budget or PreparationBudget(services.clock.monotonic_ns)
        with preparation_budget.measure(services.policy.replan_budget.total_ms) as clock_limit:
            services.advance_clock(existing_sid, deadline=clock_limit)
    now = services.clock.now()
    with services.store.transaction() as tx:
        requests = HttpRequestRepository(tx)
        old = requests.get(identity, digest)
        if old is not None:
            return old
        repo = RuntimeRepository(tx)
        sid = session_id or (requests.task_session(task_id) if task_id is not None else None)
        initial = sid is None
        if initial:
            sid = stable_id("session", identity)
            session = new_session(services, sid, mode, now)
            repo.remember_release(services.knowledge.release)
            repo.save(session)
            if task_id is not None:
                requests.map_task(task_id, sid)
        else:
            assert sid is not None
            session = repo.get(sid)
            if session.status == "ENDED":
                raise ServiceError("TASK_ENDED", "任务已经结束，请使用新的 task_id")
            if (
                base_plan_version is not None
                and base_plan_version != session.runtime.current_plan_version
            ):
                raise ServiceError("STATE_CONFLICT", "计划版本已变化，请刷新后再追加")
            if len(recipes) != 1:
                raise ServiceError("INVALID_REQUEST", "已有任务每次只能追加一道菜")
            cancelled = (
                set(session.runtime.details.cancelled_instance_ids)
                if session.runtime.details
                else set()
            )
            if recipes[0].id in {
                r.recipe_id for r in session.menu if r.recipe_instance_id.root not in cancelled
            }:
                raise ServiceError("DUPLICATE_RECIPE", "该菜已在任务中，重复份数尚未定义")
        assert sid is not None
        mode = session.runtime.execution_mode
        at = (
            session.runtime.time_origin.at(session.runtime.now_offset_sec)
            if mode == "SIMULATED"
            else session.runtime.time_origin.at(clock_offset(session, now))
            if mode == "SCHEDULE_CLOCK" and session.schedule_clock is not None
            else business_time(session.runtime.time_origin, now)
        )
        event = RuntimeEvent(
            event_id=stable_id("http-event", identity),
            session_id=sid,
            event_type="START_SESSION" if initial else "ADD_RECIPE",
            expected_state_revision=session.runtime.state_revision,
            base_plan_version=session.runtime.current_plan_version,
            occurred_at=at,
            received_at=at if mode == "SIMULATED" else now,
            source=mode,
            payload=MenuPayload(recipes=recipes),
        )
        record = HttpRequestRecord(
            request_id=identity,
            payload_hash=digest,
            session_id=sid,
            event=event,
            client_body=json.dumps(
                digest_payload
                if digest_payload is not None
                else [r.model_dump(mode="json") for r in recipes],
                ensure_ascii=False,
            ),
        )
        requests.reserve(record)
        return record


def find_http_request(
    services: ServiceContainer, identity: str, digest: str
) -> HttpRequestRecord | None:
    """先核对持久请求身份，重试不按已经改变的运行时钟重建事件。"""
    with services.store.engine.connect() as tx:
        return HttpRequestRepository(tx).get(identity, digest)


def admit_event(
    services: ServiceContainer,
    identity: str,
    digest: str,
    event: RuntimeEvent,
    client_payload: object | None = None,
) -> HttpRequestRecord:
    with services.store.transaction() as tx:
        requests = HttpRequestRepository(tx)
        old = requests.get(identity, digest)
        if old is not None:
            return old
        record = HttpRequestRecord(
            request_id=identity,
            payload_hash=digest,
            session_id=event.session_id.root,
            event=event,
            client_body=json.dumps(client_payload, ensure_ascii=False),
        )
        requests.reserve(record)
        return record
