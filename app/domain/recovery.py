"""恢复工艺的内容绑定，以及已批准重试的完整成员事实。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from app.domain.base import Digest, FrozenModel, NonEmpty, PositiveInt
from app.domain.events import LotMovement
from app.domain.ids import ExecutionId, OperationId, RecipeId, TaskId
from app.domain.resources import ResourceUse

if TYPE_CHECKING:
    from app.domain.runtime_snapshot import RuntimeSnapshot


class RecoveryBinding(FrozenModel):
    recipe_id: RecipeId
    operation_id: OperationId
    recipe_hash: Digest
    operation_hash: Digest


class ResumeProcedure(FrozenModel):
    """审核授权的同工艺续做：全部实际投入保留，并限制中断时长。"""

    continuation: Literal["SAME_PROCESS"] = "SAME_PROCESS"
    source_carrier_kind: Literal["STANDALONE", "SHARED_PREP"]
    resource_uses: tuple[ResourceUse, ...]
    retain_all_consumed: Literal[True] = True
    max_pause_sec: PositiveInt


class ResumptionEvidence(FrozenModel):
    procedure: ResumeProcedure
    retained_inputs: tuple[LotMovement, ...]
    authorized_duration_sec: PositiveInt
    original_duration_sec: PositiveInt
    authorization_event_id: NonEmpty
    parent_hash: Digest
    authorized_at: AwareDatetime
    latest_start_at: AwareDatetime
    source_kind: Literal["REVIEWED", "SYNTHETIC"]
    evidence_refs: tuple[NonEmpty, ...]


class RecoveryRuleSpec(FrozenModel):
    schema_version: Literal["recovery-rule-v1"] = "recovery-rule-v1"
    bindings: tuple[RecoveryBinding, ...]
    kind: Literal["REMAKE", "RESUME"]
    remaining_sec: PositiveInt | None = None
    resume_procedure: ResumeProcedure | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def complete_scope(self) -> Self:
        identities = {(item.recipe_id, item.operation_id) for item in self.bindings}
        if not identities or len(identities) != len(self.bindings):
            raise ValueError("恢复范围必须包含非空且不重复的菜谱/工序")
        if (self.kind == "RESUME") != (self.remaining_sec is not None):
            raise ValueError("续做必须明确剩余工艺时长，重做不得缩短原工艺")
        if self.kind == "REMAKE" and self.resume_procedure is not None:
            raise ValueError("重做不能引用保留投入的续做工艺")
        return self


def retained_input_tasks(runtime: RuntimeSnapshot) -> frozenset[TaskId]:
    return frozenset(
        task
        for record in runtime.executions
        if record.recovery_kind == "RESUME"
        and record.resumption is not None
        and record.status in {"PENDING", "RUNNING", "COMPLETED"}
        for task in record.task_ids
    )


def superseded_failures(runtime: RuntimeSnapshot) -> frozenset[ExecutionId]:
    return frozenset(
        failed.execution_id
        for failed in runtime.executions
        if failed.status == "FAILED"
        and any(
            child.previous_execution_id == failed.execution_id
            and child.recovery_rule_id is not None
            and child.recovery_kind is not None
            and child.event_refs
            and set(child.task_ids) == set(failed.task_ids)
            for child in runtime.executions
        )
    )


def pending_retry_groups(runtime: RuntimeSnapshot) -> tuple[tuple[TaskId, ...], ...]:
    parents = superseded_failures(runtime)
    return tuple(
        record.task_ids
        for record in runtime.executions
        if record.status == "PENDING" and record.previous_execution_id in parents
    )


def compatible_retry_scope(covers: tuple[TaskId, ...], runtime: RuntimeSnapshot) -> bool:
    proposed = set(covers)
    return all(
        not proposed.intersection(group) or proposed == set(group)
        for group in pending_retry_groups(runtime)
    )
