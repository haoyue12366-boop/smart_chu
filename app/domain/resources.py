from __future__ import annotations

from enum import StrEnum

from pydantic import Field, StrictInt, StrictStr, model_validator

from app.domain.base import FrozenModel, NonEmpty, PositiveInt, ReviewStatus
from app.domain.ids import CarrierId, OperationId
from app.domain.time import Interval


class ResourceType(StrEnum):
    HUMAN = "HUMAN"
    DEVICE = "DEVICE"


class ConflictPolicy(StrEnum):
    UNARY = "UNARY"
    BATCH_EXCLUSIVE = "BATCH_EXCLUSIVE"
    STATE_COMPATIBLE = "STATE_COMPATIBLE"
    SHARED_AUXILIARY = "SHARED_AUXILIARY"


class OccupationPolicy(StrEnum):
    WHOLE_INTERVAL = "WHOLE_INTERVAL"
    PHASE_INTERVAL = "PHASE_INTERVAL"


class ConfigurationValue(FrozenModel):
    parameter: NonEmpty
    value: StrictInt | StrictStr


class ParameterConstraint(FrozenModel):
    parameter: NonEmpty
    unit: str | None = None
    minimum: StrictInt | None = None
    maximum: StrictInt | None = None
    allowed_values: tuple[StrictInt | StrictStr, ...] = ()

    @model_validator(mode="after")
    def valid_range(self) -> ParameterConstraint:
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("参数范围倒置")
        return self


class DeviceProfile(FrozenModel):
    profile_id: NonEmpty
    device_type: NonEmpty
    mode: NonEmpty
    constraints: tuple[ParameterConstraint, ...] = ()
    rule_version: NonEmpty
    provenance_refs: tuple[NonEmpty, ...]
    review_status: ReviewStatus = ReviewStatus.DRAFT


class DeviceInstance(FrozenModel):
    device_instance_id: NonEmpty
    physical_resource_id: NonEmpty | None = None
    component_id: NonEmpty | None = None
    capacity: PositiveInt = Field(default=1, exclude_if=lambda value: value == 1)
    capability_refs: tuple[NonEmpty, ...] = ()
    profile_constraints: tuple[ParameterConstraint, ...] = ()
    conflict_policy: ConflictPolicy | None = None
    rule_version: NonEmpty = "unreviewed"
    evidence_refs: tuple[NonEmpty, ...] = ()
    review_status: ReviewStatus = ReviewStatus.DRAFT

    @model_validator(mode="after")
    def mapping(self) -> DeviceInstance:
        if self.review_status == ReviewStatus.APPROVED and (
            not self.physical_resource_id or not self.component_id or not self.conflict_policy
        ):
            raise ValueError("正式设备必须有物理映射与竞争策略")
        return self

    @property
    def competition_key(self) -> tuple[str, str]:
        if self.physical_resource_id is None or self.component_id is None:
            raise ValueError("设备物理映射尚未审核")
        return self.physical_resource_id, self.component_id


class ResourceUse(FrozenModel):
    resource_type: ResourceType
    resource_id: NonEmpty
    units: PositiveInt = 1
    layer_index: PositiveInt | None = Field(default=None, exclude_if=lambda value: value is None)
    occupied_layer_indices: tuple[PositiveInt, ...] = Field(
        default=(), exclude_if=lambda value: not value
    )
    physical_resource_id: NonEmpty | None = None
    component_id: NonEmpty | None = None
    profile_options: tuple[NonEmpty, ...] = ()
    configuration: tuple[ConfigurationValue, ...] = ()
    conflict_policy: ConflictPolicy | None = None
    occupation_policy: OccupationPolicy = OccupationPolicy.WHOLE_INTERVAL
    carrier_id: CarrierId | None = None
    phase_id: NonEmpty | None = None
    presence_condition: NonEmpty | None = None
    occupancy: Interval | None = None
    human_interventions: tuple[OperationId, ...] = ()
    rule_version: NonEmpty = "unreviewed"
    evidence_refs: tuple[NonEmpty, ...] = ()
    review_status: ReviewStatus = ReviewStatus.DRAFT

    @model_validator(mode="after")
    def valid_resource(self) -> ResourceUse:
        if self.occupied_layer_indices:
            if self.layer_index is not None:
                raise ValueError("固定层位集合不能同时指定单个动态层位")
            if tuple(sorted(set(self.occupied_layer_indices))) != self.occupied_layer_indices:
                raise ValueError("固定层位集合必须递增且不重复")
            if self.units != len(self.occupied_layer_indices):
                raise ValueError("固定层位数量必须与资源用量一致")
        if self.resource_type == ResourceType.HUMAN:
            if self.layer_index is not None or self.occupied_layer_indices:
                raise ValueError("人工资源不能指定设备层位")
            if self.resource_id != "human_1" or self.units != 1:
                raise ValueError("人工资源只能为 human_1，容量 1")
            if self.physical_resource_id not in (None, "human_1"):
                raise ValueError("人工物理映射不能新增人员")
            if self.conflict_policy != ConflictPolicy.UNARY:
                raise ValueError("人工资源必须独占")
        elif self.review_status == ReviewStatus.APPROVED and (
            not self.physical_resource_id or not self.component_id or not self.conflict_policy
        ):
            raise ValueError("正式执行资源缺少物理映射或竞争策略")
        return self

    @property
    def effective_layer_indices(self) -> tuple[int, ...]:
        """固定工艺占用的所有层位，或排程选择的单层；空集合表示尚未分配。"""
        return self.occupied_layer_indices or (
            (self.layer_index,) if self.layer_index is not None else ()
        )
