"""请求可重复观察，业务事件和成功发布身份只绑定一次。"""

from threading import Lock

from sqlalchemy import select

from app.domain.ports import Deadline
from app.domain.runtime_planning import RuntimePlanningResult
from app.domain.schedule import PublishedPlan
from app.services.container import ServiceContainer
from app.services.preparation_budget import PreparationBudget
from app.services.simulation_control import prepare_simulation
from app.storage import models
from app.storage.competition_tasks import HttpRequestRecord, HttpRequestRepository
from app.storage.repositories import RuntimeRepository


def execute_request(
    services: ServiceContainer,
    record: HttpRequestRecord,
    limit: Deadline,
    *,
    preparation_budget: PreparationBudget | None = None,
) -> RuntimePlanningResult:
    lock = services.request_locks.setdefault(record.request_id, Lock())
    if not lock.acquire(blocking=False):
        budget = (
            services.policy.initial_budget
            if record.event.event_type == "START_SESSION"
            else services.policy.replan_budget
        )
        return RuntimePlanningResult(status="PENDING", budget_ms=budget.total_ms)
    try:
        # 恢复队列读取后，前台可能已完成同一身份；重新观察持久回执，
        # 避免用队列中的旧对象重复进入事实事务或覆盖成功结果。
        with services.store.engine.connect() as tx:
            latest = HttpRequestRepository(tx).get(record.request_id, record.payload_hash)
        if latest is None:
            raise KeyError("请求尚未登记")
        return _execute_reserved_request(services, latest, limit, preparation_budget)
    finally:
        lock.release()


def _execute_reserved_request(
    services: ServiceContainer,
    record: HttpRequestRecord,
    limit: Deadline,
    preparation_budget: PreparationBudget | None,
) -> RuntimePlanningResult:
    if record.result is not None and record.result.status != "PENDING":
        prior = record.result
        if (
            prior.status == "FAILED"
            and prior.event is not None
            and prior.event.status == "APPLIED"
            and prior.event.requires_replan
            and record.response_body is None
        ):
            # 前轮发布提交结果不明或恢复作业随后完成时，只观察首次有效发布；
            # 不再次执行事件、扣料或申请一份新的 Solver 预算。
            recovered = _first_publication(services, record.session_id, prior.event.state_revision)
            if recovered is not None:
                result = prior.model_copy(update={"status": "PUBLISHED", "plan": recovered})
                with services.store.transaction() as tx:
                    HttpRequestRepository(tx).finish(record.request_id, result)
                return result
        return record.result
    if record.event.event_type == "ADVANCE_SIMULATION":
        lock = services.simulation_locks.setdefault(record.session_id, Lock())
        if not lock.acquire(blocking=False):
            return RuntimePlanningResult(
                status="PENDING", budget_ms=services.policy.replan_budget.total_ms
            )
        try:
            runtime, _ = services.for_session(record.session_id)
            record = prepare_simulation(runtime, record, limit)
            return _execute_admitted(services, record, limit, preparation_budget)
        finally:
            lock.release()
    return _execute_admitted(services, record, limit, preparation_budget)


def _first_publication(services: ServiceContainer, sid: str, revision: int) -> PublishedPlan | None:
    with services.store.engine.connect() as tx:
        repo = RuntimeRepository(tx)
        versions = tx.execute(
            select(models.plans.c.version)
            .where(models.plans.c.session_id == sid)
            .order_by(models.plans.c.version)
        ).scalars()
        return next(
            (
                plan
                for version in versions
                if (plan := repo.plan(sid, version)) is not None and plan.state_revision >= revision
            ),
            None,
        )


def _execute_admitted(
    services: ServiceContainer,
    record: HttpRequestRecord,
    limit: Deadline,
    preparation_budget: PreparationBudget | None,
) -> RuntimePlanningResult:
    _, planner = services.for_session(record.session_id)
    result = planner.apply_event(record.event, limit, preparation_budget=preparation_budget)
    needs_recovery = not (
        result.event
        and result.event.first_applied
        and result.status == "PUBLISHED"
        and result.plan is not None
    )
    if result.event and result.event.status == "APPLIED" and needs_recovery:
        # 进程可能在事件或计划提交后退出；优先找第一次覆盖该事件的持久发布。
        with services.store.engine.connect() as tx:
            repo = RuntimeRepository(tx)
            if result.event.requires_replan:
                rows = tx.execute(
                    select(models.plans.c.version)
                    .where(models.plans.c.session_id == record.session_id)
                    .order_by(models.plans.c.version)
                ).scalars()
                recovered = next(
                    (
                        plan
                        for version in rows
                        if (plan := repo.plan(record.session_id, version)) is not None
                        and plan.state_revision >= result.event.state_revision
                    ),
                    None,
                )
            else:
                recovered = repo.plan(record.session_id, record.event.base_plan_version)
        if recovered is not None:
            result = result.model_copy(
                update={
                    "plan": recovered,
                    "status": "PUBLISHED" if result.event.requires_replan else "NO_REPLAN",
                }
            )
    with services.store.transaction() as tx:
        HttpRequestRepository(tx).finish(record.request_id, result)
    return result
