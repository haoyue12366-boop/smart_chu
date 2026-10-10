"""持久历史与比赛任务关联的只读应用查询。"""

from typing import Literal

from app.services.container import ServiceContainer
from app.services.plan_presentation import presentation
from app.storage.competition_tasks import HttpRequestRepository
from app.storage.repositories import RuntimeRepository


def read_plan(
    services: ServiceContainer,
    session_id: str,
    version: int,
    *,
    view: Literal["full", "workbench"] = "full",
) -> dict[str, object]:
    with services.store.engine.connect() as tx:
        repo = RuntimeRepository(tx)
        plan = repo.plan(session_id, version)
        if plan is None:
            raise KeyError("历史计划不存在")
        problem = repo.problem(session_id, version)
    display = presentation(plan, problem).model_dump(mode="json")
    if view == "workbench":
        return {
            "plan": plan.model_dump(
                mode="json",
                include={
                    "session_id": True,
                    "plan_version": True,
                    "parent_plan_version": True,
                    "state_revision": True,
                    "knowledge_version": True,
                    "snapshot_id": True,
                    "time_origin": True,
                    "committed_at": True,
                    "planning_overhead": True,
                    "validated": {"candidate": {"metrics"}},
                    "serial_reference": {"candidate": {"metrics"}},
                },
            ),
            "presentation": display,
            "optimization": {
                "strategy": problem.policy.search_strategy,
                "spread_basis": problem.policy.objective.spread_basis,
                "rest_gap_sec": problem.policy.objective.rest_gap_sec,
            },
        }
    return {
        "plan": plan.model_dump(mode="json"),
        "problem": problem.model_dump(mode="json"),
        "presentation": display,
    }


def competition_session(services: ServiceContainer, task_id: str) -> dict[str, object]:
    with services.store.engine.connect() as tx:
        sid = HttpRequestRepository(tx).task_session(task_id)
        if sid is None:
            raise KeyError("比赛任务不存在")
        session = RuntimeRepository(tx).get(sid)
    return {
        "task_id": task_id,
        "session_id": sid,
        "plan_version": session.runtime.current_plan_version,
    }
