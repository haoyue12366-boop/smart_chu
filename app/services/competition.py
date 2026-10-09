"""比赛准入、执行、独立响应校验与短事务绑定；协议投影由调用方注入。"""

from collections.abc import Callable
from uuid import uuid4

from app.domain.candidates import stable_id
from app.domain.competition_contract import RecipeSelection
from app.domain.competition_decimal import DecimalCompetitionResponse
from app.domain.errors import ServiceError
from app.domain.events import EventRecipe, EventSource
from app.domain.knowledge import MenuKnowledgeView
from app.domain.ports import Deadline
from app.domain.schedule import PublishedPlan
from app.domain.scheduling_problem import SchedulingProblem
from app.services.container import ServiceContainer
from app.services.http_requests import admit_menu
from app.services.preparation_budget import PreparationBudget
from app.services.request_execution import execute_request
from app.storage.competition_tasks import HttpRequestRepository
from app.storage.repositories import RuntimeRepository
from app.validation.competition_contract import validate_competition_projection

Projection = Callable[
    [PublishedPlan, SchedulingProblem, MenuKnowledgeView], DecimalCompetitionResponse
]


def process_competition_request(
    services: ServiceContainer,
    choices: list[RecipeSelection],
    task_id: str | None,
    key: str | None,
    started_ns: int,
    project: Projection,
) -> str:
    if not choices:
        raise ServiceError("INVALID_REQUEST", "初排菜单不能为空")
    if any(isinstance(choice.id, int) for choice in choices):
        raise ServiceError("UNKNOWN_RECIPE", "整数 ID 尚无官方映射，请使用菜谱真实字符串 ID")
    recipes = tuple(EventRecipe(id=str(choice.id), name=choice.name) for choice in choices)
    identity = stable_id("competition-http", task_id or "independent", key or str(uuid4()))
    preparation_budget = PreparationBudget(services.clock.monotonic_ns, started_ns)
    record = admit_menu(
        services,
        recipes,
        identity,
        task_id=task_id,
        mode=EventSource.SCHEDULE_CLOCK,
        preparation_budget=preparation_budget,
    )
    if record.response_body is not None:
        return record.response_body
    budget = (
        services.policy.initial_budget
        if record.event.event_type == "START_SESSION"
        else services.policy.replan_budget
    )
    limit = Deadline(expires_at_ns=started_ns + budget.total_ms * 1_000_000)
    computation_limit = Deadline(
        # 独立响应扫描和问题重载也服从原 HTTP 截止时间；提前结束可选优化。
        expires_at_ns=limit.expires_at_ns - 400 * 1_000_000
    )
    result = execute_request(
        services, record, computation_limit, preparation_budget=preparation_budget
    )
    if result.status == "EVENT_REJECTED":
        raise ServiceError(
            "STATE_CONFLICT",
            (result.event.rejection_reason or "事件未接受") if result.event else "事件未接受",
        )
    if result.status == "PENDING":
        raise ServiceError("PLANNING_PENDING", "事件已接受，等待有效重排；使用相同幂等键查询结果")
    if result.status == "FAILED" or result.plan is None:
        raise ServiceError("NO_FEASIBLE_PLAN", "事件已保存，本次未发布完整合法计划")
    with services.store.engine.connect() as tx:
        problem = RuntimeRepository(tx).problem(record.session_id, result.plan.plan_version)
    knowledge = services.knowledge_for(record.session_id)
    response = project(result.plan, problem, knowledge)
    try:
        validate_competition_projection(
            response, result.plan, problem, knowledge, timezone=services.settings.timezone
        )
    except ValueError as exc:
        raise ServiceError("STATE_INCOMPLETE", "比赛响应未通过已发布计划的独立事实校验") from exc
    body = response.model_dump_json(exclude_none=True)
    with services.store.transaction() as tx:
        body = HttpRequestRepository(tx).bind_response(identity, result.plan, body)
    if services.clock.monotonic_ns() > preparation_budget.extend(limit).expires_at_ns:
        raise TimeoutError("响应序列化已超预算；已绑定结果可用原幂等键查询")
    return body
