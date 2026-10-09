from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from app.domain.base import FrozenModel, NonEmpty, NonNegativeInt
from app.domain.events import EventSource, LotMovement
from app.domain.ids import EventId, ExecutionId, MaterialLotId, SessionId, TaskId
from app.domain.quantity import ScaledQuantity
from app.domain.recovery import ResumptionEvidence
from app.domain.resources import ConfigurationValue
from app.domain.runtime_facts import ActualResourceSpan, RuntimeDetails, TaskSpan
from app.domain.time import TimeOrigin
from app.domain.transitions import ThermalState


class AvailabilityStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


class OccupancyStatus(StrEnum):
    FREE = "FREE"
    OCCUPIED = "OCCUPIED"
    AWAITING_RELEASE_CONFIRMATION = "AWAITING_RELEASE_CONFIRMATION"


class ExecutionStatus(StrEnum):
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class DeviceState(FrozenModel):
    device_instance_id: NonEmpty
    physical_resource_id: NonEmpty
    component_id: NonEmpty
    availability_status: AvailabilityStatus
    occupancy_status: OccupancyStatus
    active_execution_id: ExecutionId | None = None
    observed_at: AwareDatetime
    source: EventSource
    expected_recovery_at: AwareDatetime | None = None
    release_confirmation_event_id: EventId | None = None
    configuration: tuple[ConfigurationValue, ...] = ()
    thermal_state: ThermalState | None = None

    @model_validator(mode="after")
    def occupancy(self) -> Self:
        if self.occupancy_status != OccupancyStatus.FREE and self.active_execution_id is None:
            raise ValueError("占用必须对应执行身份")
        if self.occupancy_status == OccupancyStatus.FREE and self.active_execution_id is not None:
            raise ValueError("空闲设备不能仍指向活动执行")
        return self


class ExecutionRecord(FrozenModel):
    execution_id: ExecutionId
    task_ids: tuple[TaskId, ...]
    status: ExecutionStatus
    source: EventSource
    event_refs: tuple[EventId, ...]
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    consumed: tuple[LotMovement, ...] = ()
    produced: tuple[LotMovement, ...] = ()
    resource_ids: tuple[NonEmpty, ...] = ()
    previous_execution_id: ExecutionId | None = None
    recovery_rule_id: NonEmpty | None = Field(default=None, exclude_if=lambda value: value is None)
    recovery_kind: Literal["REMAKE", "RESUME"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    resumption: ResumptionEvidence | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    failure_output_status: Literal["QUALIFIED", "UNKNOWN", "WASTE"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    remaining_sec: NonNegativeInt | None = None
    remaining_source_ref: NonEmpty | None = None
    remaining_observed_at: AwareDatetime | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    task_spans: tuple[TaskSpan, ...] = Field(default=(), exclude_if=lambda v: not v)
    resource_spans: tuple[ActualResourceSpan, ...] = Field(default=(), exclude_if=lambda v: not v)
    carrier_id: NonEmpty | None = Field(default=None, exclude_if=lambda v: v is None)
    started_task_ids: tuple[TaskId, ...] = Field(default=(), exclude_if=lambda v: not v)
    completed_task_ids: tuple[TaskId, ...] = Field(default=(), exclude_if=lambda v: not v)
    scheduled_resource_spans: tuple[ActualResourceSpan, ...] = Field(
        default=(), exclude_if=lambda v: not v
    )
    interruption_event_refs: tuple[EventId, ...] = Field(default=(), exclude_if=lambda v: not v)

    @model_validator(mode="after")
    def fact_evidence(self) -> Self:
        if self.remaining_sec is not None and not self.remaining_source_ref:
            raise ValueError("剩余时长必须有明确来源")
        if self.status in (
            ExecutionStatus.RUNNING,
            ExecutionStatus.COMPLETED,
            ExecutionStatus.FAILED,
        ):
            if self.started_at is None or not self.event_refs:
                raise ValueError("执行事实必须有来源事件和开始时刻")
        if (
            self.status in (ExecutionStatus.COMPLETED, ExecutionStatus.FAILED)
            and self.finished_at is None
        ):
            raise ValueError("已结束执行必须保留实际结束时刻")
        if self.started_at and self.finished_at and self.finished_at < self.started_at:
            raise ValueError("实际结束早于开始")
        return self


class MaterialLot(FrozenModel):
    lot_id: MaterialLotId
    spec_id: NonEmpty
    produced_by_execution_id: ExecutionId | None = None
    produced_at: AwareDatetime
    quantity_produced: ScaledQuantity
    quantity_available: ScaledQuantity
    quantity_reserved: ScaledQuantity
    storage_state: NonEmpty
    source_lot_ids: tuple[MaterialLotId, ...] = ()
    source_event_id: EventId
    version: NonNegativeInt
    quality_status: Literal["QUALIFIED", "UNKNOWN", "WASTE"] = "UNKNOWN"


class RuntimeSnapshot(FrozenModel):
    session_id: SessionId
    state_revision: NonNegativeInt
    current_plan_version: NonNegativeInt
    knowledge_version: NonEmpty
    rule_version: NonEmpty
    snapshot_id: NonEmpty
    time_origin: TimeOrigin
    now_offset_sec: NonNegativeInt
    execution_mode: EventSource
    executions: tuple[ExecutionRecord, ...] = ()
    material_lots: tuple[MaterialLot, ...] = ()
    device_states: tuple[DeviceState, ...] = ()
    current_plan_ref: NonEmpty | None = None
    event_refs: tuple[EventId, ...] = ()
    details: RuntimeDetails | None = Field(default=None, exclude_if=lambda v: v is None)
