"""用户授权的开工前备料声明，与实测完成及实际余料供应分开。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from app.domain.base import Digest, FrozenModel, NonEmpty, NonNegativeInt
from app.domain.ids import OperationId, RecipeId, RecipeInstanceId, TaskId

if TYPE_CHECKING:
    from app.domain.runtime_snapshot import RuntimeSnapshot


class AdvancePreparationRule(FrozenModel):
    rule_id: NonEmpty
    recipe_id: RecipeId
    recipe_hash: Digest
    operation_ids: tuple[OperationId, ...]
    description: NonEmpty
    evidence_refs: tuple[NonEmpty, ...]


class AdvancePreparation(FrozenModel):
    preparation_id: NonEmpty
    rule_id: NonEmpty
    recipe_instance_id: RecipeInstanceId
    recipe_id: RecipeId
    task_ids: tuple[TaskId, ...]
    operation_ids: tuple[OperationId, ...]
    available_at_sec: NonNegativeInt
    original_duration_sec: NonNegativeInt
    source_kind: Literal["USER_POLICY_ASSUMPTION"] = "USER_POLICY_ASSUMPTION"
    evidence_refs: tuple[NonEmpty, ...]
    description: NonEmpty

    @property
    def satisfied_at_sec(self) -> int:
        """仅供逻辑前置满足；不是加工完成时间。"""
        return self.available_at_sec


def active_preparations(runtime: RuntimeSnapshot) -> tuple[AdvancePreparation, ...]:
    if runtime.details is None:
        return ()
    return tuple(
        item
        for item in runtime.details.advance_preparations
        if item.recipe_instance_id.root not in runtime.details.cancelled_instance_ids
    )
