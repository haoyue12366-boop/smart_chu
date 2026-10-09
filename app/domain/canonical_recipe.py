"""菜谱知识表达；设备能力和完整路径可行性由 P1 独立判断。"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import AwareDatetime, model_validator

from app.domain.base import (
    Digest,
    FrozenModel,
    NonEmpty,
    NonNegativeInt,
    ReviewStatus,
    content_hash,
)
from app.domain.ids import OperationId, RecipeId
from app.domain.material import MaterialLoss, MaterialRequirement, MaterialSpec
from app.domain.resources import ResourceType, ResourceUse


class Action(StrEnum):
    WASH = "WASH"
    CUT = "CUT"
    MIX = "MIX"
    PREPARE = "PREPARE"
    MARINATE = "MARINATE"
    WAIT = "WAIT"
    CHILL = "CHILL"
    FREEZE = "FREEZE"
    HEAT = "HEAT"
    PREHEAT = "PREHEAT"
    LOAD = "LOAD"
    UNLOAD = "UNLOAD"
    ADD = "ADD"
    STIR = "STIR"
    TRANSFER = "TRANSFER"
    FINISH = "FINISH"
    MILESTONE = "MILESTONE"
    UNKNOWN = "UNKNOWN"


class DurationSpec(FrozenModel):
    nominal_sec: NonNegativeInt | None = None
    execution_sec: NonNegativeInt | None = None
    lower_sec: NonNegativeInt | None = None
    upper_sec: NonNegativeInt | None = None
    model_id: NonEmpty | None = None
    quantity_basis: MaterialRequirement | None = None
    source_ref: NonEmpty | None = None
    fixed_process_time: bool = False

    @model_validator(mode="after")
    def bounds(self) -> Self:
        if self.lower_sec is not None and self.upper_sec is not None:
            if self.lower_sec > self.upper_sec:
                raise ValueError("时长上下界倒置")
        if self.execution_sec is not None:
            if self.lower_sec is not None and self.execution_sec < self.lower_sec:
                raise ValueError("选用时长小于工艺下界")
            if self.upper_sec is not None and self.execution_sec > self.upper_sec:
                raise ValueError("选用时长大于工艺上界")
        return self


class Intervention(FrozenModel):
    operation_id: OperationId
    offset_min_sec: NonNegativeInt
    offset_max_sec: NonNegativeInt

    @model_validator(mode="after")
    def window(self) -> Self:
        if self.offset_min_sec > self.offset_max_sec:
            raise ValueError("介入窗口倒置")
        return self


class BatchPolicy(StrEnum):
    STANDALONE = "STANDALONE"
    STRICT_TOGETHER = "STRICT_TOGETHER"
    FIXED_RECIPE = "FIXED_RECIPE"


class ExecutionPolicy(FrozenModel):
    interruptible: bool = False
    interventions: tuple[Intervention, ...] = ()
    batch_policy: BatchPolicy = BatchPolicy.STANDALONE
    fixed_batch_id: NonEmpty | None = None
    thermal_group_id: NonEmpty | None = None
    shared_prep_rule_ids: tuple[NonEmpty, ...] = ()
    holding_min_sec: NonNegativeInt | None = None
    holding_max_sec: NonNegativeInt | None = None


class OperationTemplate(FrozenModel):
    operation_id: OperationId
    action: Action
    description: str = ""
    required: bool = True
    duration: DurationSpec
    material_inputs: tuple[MaterialRequirement, ...] = ()
    material_outputs: tuple[MaterialRequirement, ...] = ()
    losses: tuple[MaterialLoss, ...] = ()
    resource_requirements: tuple[ResourceUse, ...] = ()
    execution_policy: ExecutionPolicy = ExecutionPolicy()
    provenance_refs: tuple[NonEmpty, ...] = ()
    review_status: ReviewStatus = ReviewStatus.DRAFT

    @model_validator(mode="after")
    def timing(self) -> Self:
        if self.duration.execution_sec == 0 and (
            self.action != Action.MILESTONE or self.resource_requirements
        ):
            raise ValueError("物理操作时长必须大于零；只有无资源逻辑里程碑可为零")
        if self.review_status == ReviewStatus.APPROVED:
            self.check_approved()
        return self

    def check_approved(self) -> None:
        if self.required and (
            self.duration.execution_sec is None or self.duration.source_ref is None
        ):
            raise ValueError("审核路径存在未知必需时长或来源")
        if self.action == Action.UNKNOWN or not self.provenance_refs:
            raise ValueError("审核路径动作和来源必须明确")
        for resource in self.resource_requirements:
            if resource.resource_type == ResourceType.DEVICE and (
                not resource.physical_resource_id
                or not resource.component_id
                or resource.conflict_policy is None
            ):
                raise ValueError("审核路径缺少设备物理映射或竞争策略")


class Dependency(FrozenModel):
    predecessor_id: OperationId
    successor_id: OperationId
    min_lag_sec: NonNegativeInt = 0
    max_lag_sec: NonNegativeInt | None = None
    reason: NonEmpty
    evidence_refs: tuple[NonEmpty, ...]

    @model_validator(mode="after")
    def lag(self) -> Self:
        if self.predecessor_id == self.successor_id:
            raise ValueError("工序不能依赖自身")
        if self.max_lag_sec is not None and self.min_lag_sec > self.max_lag_sec:
            raise ValueError("依赖间隔上下界倒置")
        return self


class ReviewStamp(FrozenModel):
    review_id: NonEmpty
    reviewer: NonEmpty
    reviewed_at: AwareDatetime
    evidence_refs: tuple[NonEmpty, ...]
    approved_content_hash: Digest

    @model_validator(mode="after")
    def evidence(self) -> Self:
        if not self.evidence_refs:
            raise ValueError("批准记录需要审核证据")
        return self


class CanonicalRecipeModel(FrozenModel):
    schema_version: NonEmpty
    recipe_id: RecipeId
    name: NonEmpty
    recipe_version: NonEmpty
    ingredient_requirements: tuple[MaterialRequirement, ...]
    material_specs: tuple[MaterialSpec, ...]
    operations: tuple[OperationTemplate, ...]
    dependencies: tuple[Dependency, ...]
    provenance_refs: tuple[NonEmpty, ...]
    review_status: ReviewStatus = ReviewStatus.DRAFT
    approval: ReviewStamp | None = None
    review_patch_refs: tuple[NonEmpty, ...] = ()
    issue_refs: tuple[NonEmpty, ...] = ()

    def semantic_hash(self) -> str:
        # 审核时刻、补丁索引和审核状态不属于工艺内容。
        payload = self.model_dump(
            mode="json", exclude={"approval", "review_patch_refs", "review_status", "issue_refs"}
        )
        import hashlib
        import json

        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    @model_validator(mode="after")
    def references_and_approval(self) -> Self:
        operations = {op.operation_id for op in self.operations}
        specs = {spec.spec_id for spec in self.material_specs}
        if len(operations) != len(self.operations) or len(specs) != len(self.material_specs):
            raise ValueError("工序或物料规格 ID 重复")
        for dependency in self.dependencies:
            if (
                dependency.predecessor_id not in operations
                or dependency.successor_id not in operations
            ):
                raise ValueError("悬空工序引用")
        for op in self.operations:
            if any(i.operation_id not in operations for i in op.execution_policy.interventions):
                raise ValueError("悬空介入工序引用")
            for material in (*op.material_inputs, *op.material_outputs):
                if material.spec_id not in specs:
                    raise ValueError("悬空物料规格引用")
        for material in self.ingredient_requirements:
            if material.spec_id not in specs:
                raise ValueError("食材引用未知物料规格")
        if self.review_status == ReviewStatus.APPROVED:
            if not self.operations or not self.provenance_refs:
                raise ValueError("审核路径不能为空")
            for op in self.operations:
                op.check_approved()
            if self.approval is None or self.approval.approved_content_hash != self.semantic_hash():
                raise ValueError("缺少匹配当前工艺版本的审核记录")
        elif self.approval is not None:
            raise ValueError("未批准版本不能保留有效批准标记")
        return self

    @property
    def document_hash(self) -> str:
        return content_hash(self)
