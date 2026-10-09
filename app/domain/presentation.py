"""只读图形展示契约；秒级时间来自已发布问题和固定事实。"""

from typing import Literal, Self

from pydantic import model_validator

from app.domain.base import FrozenModel, NonNegativeInt, PositiveInt
from app.domain.resources import ConfigurationValue
from app.domain.time import TimeOrigin


class DisplayInterval(FrozenModel):
    start_sec: NonNegativeInt
    end_sec: NonNegativeInt

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.end_sec < self.start_sec:
            raise ValueError("显示区间倒置")
        return self


class OperationPresentation(DisplayInterval):
    task_id: str
    carrier_id: str
    recipe_id: str
    recipe_instance_id: str
    recipe_name: str
    title: str
    action: str
    shared: bool
    frozen: bool
    inventory_supplied: bool = False


class ResourcePresentation(DisplayInterval):
    entry_id: str
    resource_id: str
    component_id: str
    resource_label: str
    device_label: str | None = None
    layer_index: PositiveInt | None = None
    title: str
    task_ids: tuple[str, ...]
    recipe_instance_ids: tuple[str, ...]
    recipe_names: tuple[str, ...]
    shared: bool
    frozen: bool
    configuration: tuple[ConfigurationValue, ...]
    reuse_intervals: tuple[DisplayInterval, ...] = ()


class AdvancePreparationPresentation(FrozenModel):
    preparation_id: str
    recipe_instance_id: str
    recipe_id: str
    recipe_name: str
    task_ids: tuple[str, ...]
    description: str
    original_duration_sec: NonNegativeInt
    source_kind: Literal["USER_POLICY_ASSUMPTION"]


class PlanPresentation(FrozenModel):
    time_origin: TimeOrigin
    range_end_sec: NonNegativeInt
    operations: tuple[OperationPresentation, ...]
    resources: tuple[ResourcePresentation, ...]
    resource_lanes: tuple[str, ...] = ()
    advance_preparations: tuple[AdvancePreparationPresentation, ...] = ()


class DevicePresentation(FrozenModel):
    device_instance_id: str
    physical_resource_id: str
    component_id: str
    availability_status: Literal["AVAILABLE", "UNAVAILABLE", "UNKNOWN", "UNOBSERVED"]
    occupancy_status: Literal["FREE", "OCCUPIED", "AWAITING_RELEASE_CONFIRMATION", "UNOBSERVED"]
    active_execution_id: str | None
    active_execution_ids: tuple[str, ...]
    configuration: tuple[ConfigurationValue, ...]

    @model_validator(mode="after")
    def occupation_has_identity(self) -> Self:
        if bool(self.active_execution_ids) != (self.active_execution_id is not None):
            raise ValueError("显示占用身份不一致")
        if (
            self.occupancy_status in {"OCCUPIED", "AWAITING_RELEASE_CONFIRMATION"}
            and not self.active_execution_ids
        ):
            raise ValueError("占用显示须来自实际执行")
        return self
