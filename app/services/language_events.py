"""语言意图在最新状态上校验，明确结果通过统一业务事件提交。"""

import asyncio

from app.domain.candidates import stable_id
from app.domain.errors import ServiceError
from app.domain.events import MenuPayload, RuntimeEvent
from app.domain.http_time import business_time
from app.domain.intent import IntentContext, LanguageRequest
from app.domain.ports import Deadline
from app.domain.runtime_clock import clock_offset
from app.services.container import ServiceContainer
from app.services.http_requests import admit_event, request_digest
from app.services.preparation_budget import PreparationBudget
from app.services.request_execution import execute_request
from app.storage.competition_tasks import HttpRequestRepository


async def process_language_request(
    services: ServiceContainer, session_id: str, body: LanguageRequest, started_ns: int
) -> dict[str, object]:
    digest = request_digest(body.model_dump(mode="json"))
    try:
        result = await _process_language_request(services, session_id, body, started_ns)
    except ServiceError as exc:
        await services.intents.record_outcome(
            session_id,
            body.event_id,
            digest,
            {"status": "HTTP_ERROR", "event_id": None, "error_code": exc.code},
        )
        raise
    outcome: dict[str, object] = {"status": result["status"], "event_id": None}
    event = result.get("event")
    if isinstance(event, dict):
        outcome.update(event_id=event["event_id"], event_status=event["status"])
    plan = result.get("plan")
    if isinstance(plan, dict):
        outcome.update(publication_id=plan["publication_id"], plan_version=plan["plan_version"])
    await services.intents.record_outcome(session_id, body.event_id, digest, outcome)
    return result


async def _process_language_request(
    services: ServiceContainer, session_id: str, body: LanguageRequest, started_ns: int
) -> dict[str, object]:
    runtime, _ = await asyncio.to_thread(services.for_session, session_id)
    preparation_budget = PreparationBudget(services.clock.monotonic_ns, started_ns)
    with preparation_budget.measure(runtime.get(session_id).policy.replan_budget.total_ms) as limit:
        session = await asyncio.to_thread(services.advance_clock, session_id, deadline=limit)
    knowledge = runtime.knowledge
    context = IntentContext(
        session_id=session_id,
        state_revision=body.expected_state_revision,
        plan_version=body.base_plan_version,
        time_origin=session.runtime.time_origin.start_at,
        menu=tuple(
            {
                "recipe_id": item.recipe_id.root,
                "recipe_instance_id": item.recipe_instance_id.root,
                "name": item.name,
            }
            for item in session.menu
        ),
        catalog=tuple(
            {"id": recipe.recipe_id.root, "name": recipe.name} for recipe in knowledge.recipes
        ),
    )
    digest = request_digest(body.model_dump(mode="json"))
    intent = await services.intents.interpret(body.text, context, body.event_id, digest)
    if intent.action in {"CLARIFY", "UNKNOWN"}:
        return {
            "status": "NEEDS_CLARIFICATION" if intent.action == "CLARIFY" else "NOT_UNDERSTOOD",
            "question": intent.question,
            "options": intent.options,
            "event_accepted": False,
        }
    identity = stable_id("language-event", session_id, body.event_id)
    with services.store.engine.connect() as tx:
        prior = HttpRequestRepository(tx).get(identity, digest)
    if prior is not None and prior.result is not None and prior.result.status != "PENDING":
        return prior.result.model_dump(mode="json")
    with preparation_budget.measure(session.policy.replan_budget.total_ms) as clock_limit:
        session = await asyncio.to_thread(services.advance_clock, session_id, deadline=clock_limit)
    if (session.runtime.state_revision, session.runtime.current_plan_version) != (
        body.expected_state_revision,
        body.base_plan_version,
    ):
        raise ServiceError("STATE_CONFLICT", "理解指令期间状态已变化，请基于最新状态重新提交")
    if intent.action == "ADD_RECIPE":
        recipe = next((r for r in knowledge.recipes if r.recipe_id.root == intent.recipe_id), None)
        if recipe is None:
            raise ServiceError("UNKNOWN_RECIPE", "意图引用未知菜谱，本次没有提交事件")
        payload = MenuPayload(recipes=({"id": recipe.recipe_id.root, "name": recipe.name},))
    else:
        instance = next(
            (r for r in session.menu if r.recipe_instance_id.root == intent.recipe_instance_id),
            None,
        )
        if instance is None:
            raise ServiceError("INVALID_REQUEST", "意图引用菜单外实例")
        if intent.action == "CANCEL_RECIPE" and any(
            execution.started_at is not None
            and any(
                task.root
                == stable_id("task", instance.recipe_instance_id.root, operation.operation_id.root)
                for task in execution.task_ids
                for recipe in knowledge.recipes
                if recipe.recipe_id == instance.recipe_id
                for operation in recipe.operations
            )
            for execution in session.runtime.executions
        ):
            raise ServiceError("INVALID_REQUEST", "自然语言只允许取消尚未开始的菜品")
        earliest = None
        if intent.action == "DELAY_RECIPE":
            assert intent.earliest_start_at is not None
            earliest = session.runtime.time_origin.offset(
                business_time(session.runtime.time_origin, intent.earliest_start_at)
            )
        payload = MenuPayload(
            recipe_instance_id=instance.recipe_instance_id, earliest_start_sec=earliest
        )
    now = runtime.clock.now()
    event = RuntimeEvent(
        event_id=identity,
        session_id=session_id,
        event_type=intent.action,
        expected_state_revision=body.expected_state_revision,
        base_plan_version=body.base_plan_version,
        occurred_at=(
            session.runtime.time_origin.at(clock_offset(session, now))
            if session.schedule_clock is not None
            else business_time(session.runtime.time_origin, now)
        ),
        received_at=now,
        source=session.runtime.execution_mode,
        payload=payload,
    )
    record = await asyncio.to_thread(
        admit_event, services, identity, digest, event, body.model_dump(mode="json")
    )
    # 语言解释和业务计算保留原上限；同步和编译单独计量，不重复补回此前同步。
    limit = Deadline(
        expires_at_ns=min(
            started_ns + 8_000_000_000,
            services.clock.monotonic_ns()
            - preparation_budget.excluded_ns
            + services.policy.replan_budget.total_ms * 1_000_000,
        )
    )
    result = await asyncio.to_thread(
        execute_request, services, record, limit, preparation_budget=preparation_budget
    )
    return result.model_dump(mode="json")
