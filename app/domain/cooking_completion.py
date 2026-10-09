"""有审核依据的出锅边界；与包含装盘的全流程完成时间分开。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Literal, Self

from pydantic import Field, model_validator

from app.domain.base import FrozenModel, NonEmpty
from app.domain.ids import OperationId, RecipeInstanceId, TaskId
from app.domain.time import Interval

if TYPE_CHECKING:
    from app.domain.scheduling_problem import SchedulingProblem

CookingCompletionKind = Literal["OUT_OF_POT", "NO_HEAT_READY"]


class CookingCompletionRule(FrozenModel):
    operation_ids: tuple[OperationId, ...] = Field(min_length=1)
    kind: CookingCompletionKind
    evidence_refs: tuple[NonEmpty, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def distinct_operations(self) -> Self:
        if len(set(self.operation_ids)) != len(self.operation_ids):
            raise ValueError("出锅工艺锚点重复")
        return self


class RecipeCookingCompletion(FrozenModel):
    recipe_instance_id: RecipeInstanceId
    task_ids: tuple[TaskId, ...] = Field(min_length=1)
    kind: CookingCompletionKind
    evidence_refs: tuple[NonEmpty, ...] = Field(min_length=1)


def cooking_finish_times(
    problem: SchedulingProblem, intervals: Mapping[TaskId, Interval]
) -> dict[RecipeInstanceId, int]:
    """读取真实/计划任务端口；成员偏移已经由调用方的端口投影处理。"""
    return {
        boundary.recipe_instance_id: max(intervals[t].end_sec for t in boundary.task_ids)
        for boundary in problem.cooking_completions
    }
