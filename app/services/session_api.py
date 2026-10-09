"""内部会话入口复用 P4 事实与发布链。"""

from datetime import datetime

from app.domain.candidates import stable_id
from app.domain.competition_contract import validate_request
from app.domain.errors import ServiceError
from app.domain.events import EventRecipe, EventSource, MenuPayload, RuntimeEvent
from app.domain.ports import Deadline
from app.domain.runtime_planning import RuntimePlanningResult
from app.services.container import ServiceContainer
from app.storage.repositories import RuntimeRepository


def check_recipes(recipes: tuple[EventRecipe, ...], services: ServiceContainer) -> None:
    catalog = {recipe.recipe_id.root: recipe.name for recipe in services.knowledge.recipes}
    try:
        validate_request([recipe.model_dump(mode="json") for recipe in recipes], catalog)
    except ValueError as exc:
        message = str(exc)
        code = message.split("：")[0]
        raise ServiceError(code, message) from exc


def create_session(
    services: ServiceContainer,
    recipes: tuple[EventRecipe, ...],
    event_id: str,
    mode: EventSource,
    start_at: datetime,
    limit: Deadline,
) -> tuple[str, RuntimePlanningResult]:
    check_recipes(recipes, services)
    sid = stable_id("session", event_id)
    # 创建身份与事件 ID 固定；短事务保护首次并发，重试不另建会话。
    with services.store.transaction() as tx:
        repo = RuntimeRepository(tx)
        try:
            session = repo.get(sid)
        except KeyError:
            session = None
        if session is None:
            from app.domain.runtime_facts import RuntimeDetails
            from app.domain.runtime_session import RuntimeSession
            from app.domain.runtime_snapshot import RuntimeSnapshot
            from app.domain.time import TimeOrigin

            repo.remember_release(services.knowledge.release)
            session = RuntimeSession(
                runtime=RuntimeSnapshot(
                    session_id=sid,
                    state_revision=0,
                    current_plan_version=0,
                    knowledge_version=services.knowledge.release.knowledge_version,
                    rule_version=services.knowledge.release.rule_version,
                    snapshot_id=services.knowledge.release.snapshot_id,
                    time_origin=TimeOrigin(start_at=start_at),
                    now_offset_sec=0,
                    execution_mode=mode,
                    details=RuntimeDetails(),
                ),
                policy=services.policy,
                knowledge_release_id=services.knowledge.release.release_id,
            )
            repo.save(session)
    runtime, planner = services.for_session(sid)
    original = runtime.get(sid)
    if original.runtime.execution_mode != mode:
        raise ServiceError("IDEMPOTENCY_CONFLICT", "同一请求不能改变执行模式")
    event = RuntimeEvent(
        event_id=event_id,
        session_id=sid,
        event_type="START_SESSION",
        expected_state_revision=0,
        base_plan_version=0,
        occurred_at=original.runtime.time_origin.start_at,
        received_at=original.runtime.time_origin.start_at,
        source=mode,
        payload=MenuPayload(recipes=recipes),
    )
    return sid, planner.apply_event(event, limit)
