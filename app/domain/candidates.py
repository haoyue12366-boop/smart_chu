"""编译中间结果与稳定身份，不包含具体求解器。"""

import hashlib
import json

from app.domain.base import FrozenModel
from app.domain.compatibility import GroupContext
from app.domain.ids import TaskId
from app.domain.ports import Deadline
from app.domain.scheduling_problem import (
    CandidateCarrier,
    LogicalTask,
    RecipeInstance,
    TaskDependency,
)


def stable_id(kind: str, *parts: str) -> str:
    payload = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return kind + "-" + hashlib.sha256(payload.encode()).hexdigest()


class InstantiationResult(FrozenModel):
    menu: tuple[RecipeInstance, ...]
    tasks: tuple[LogicalTask, ...]
    dependencies: tuple[TaskDependency, ...]
    completed_task_ids: tuple[TaskId, ...] = ()
    running_task_ids: tuple[TaskId, ...] = ()


class SharedCandidateContext(FrozenModel):
    instantiated: InstantiationResult
    group: GroupContext
    standalone: tuple[CandidateCarrier, ...]
    deadline: Deadline


# 同一载体契约覆盖多个需求，具体kind由生成器保证。
SharedPrepCandidate = CandidateCarrier
ThermalBatchCandidate = CandidateCarrier
