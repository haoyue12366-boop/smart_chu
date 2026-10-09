"""运行事件只是事实输入契约，不在此处执行状态转换。"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from app.domain.base import FrozenModel, NonEmpty, NonNegativeInt
from app.domain.ids import (
    EventId,
    ExecutionId,
    MaterialLotId,
    RecipeId,
    RecipeInstanceId,
    SessionId,
    TaskId,
)
from app.domain.quantity import ScaledQuantity
from app.domain.resources import ConfigurationValue
from app.domain.runtime_facts import RationalAmount


class EventSource(StrEnum):
    SIMULATED = "SIMULATED"
    SCHEDULE_CLOCK = "SCHEDULE_CLOCK"
    MANUAL_CONFIRM = "MANUAL_CONFIRM"
    DEVICE_FEEDBACK = "DEVICE_FEEDBACK"
    COMPETITION_PROFILE = "COMPETITION_PROFILE"


class EventType(StrEnum):
    START_SESSION = "START_SESSION"
    ADD_RECIPE = "ADD_RECIPE"
    CANCEL_RECIPE = "CANCEL_RECIPE"
    DELAY_RECIPE = "DELAY_RECIPE"
    OPERATION_STARTED = "OPERATION_STARTED"
    OPERATION_COMPLETED = "OPERATION_COMPLETED"
    DURATION_UPDATED = "DURATION_UPDATED"
    OPERATION_FAILED = "OPERATION_FAILED"
    DEVICE_UNAVAILABLE = "DEVICE_UNAVAILABLE"
    DEVICE_RECOVERED = "DEVICE_RECOVERED"
    DEVICE_RELEASE_CONFIRMED = "DEVICE_RELEASE_CONFIRMED"
    DEVICE_STATE_UPDATED = "DEVICE_STATE_UPDATED"
    MATERIAL_SHORTAGE = "MATERIAL_SHORTAGE"
    MATERIAL_ADJUSTED = "MATERIAL_ADJUSTED"
    MANUAL_OVERRIDE = "MANUAL_OVERRIDE"
    OPERATION_RETRY_REQUESTED = "OPERATION_RETRY_REQUESTED"
    ADVANCE_SIMULATION = "ADVANCE_SIMULATION"
    RESET_SESSION = "RESET_SESSION"
    REPLAN_REQUESTED = "REPLAN_REQUESTED"


class EventRecipe(FrozenModel):
    id: RecipeId
    name: NonEmpty


class MenuPayload(FrozenModel):
    recipes: tuple[EventRecipe, ...] = ()
    recipe_instance_id: RecipeInstanceId | None = None
    earliest_start_sec: NonNegativeInt | None = None


class LotMovement(FrozenModel):
    lot_id: MaterialLotId
    quantity: ScaledQuantity | None = None
    spec_id: NonEmpty
    batch_share: RationalAmount | None = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def amount_kind(self) -> Self:
        if (self.quantity is None) == (self.batch_share is None):
            raise ValueError("物料变化须明确数值数量或原配方整批份额之一")
        return self


class ExecutionPayload(FrozenModel):
    task_id: TaskId
    execution_id: ExecutionId
    reason: NonEmpty | None = None
    consumed: tuple[LotMovement, ...] = ()
    produced: tuple[LotMovement, ...] = ()
    output_status: Literal["QUALIFIED", "UNKNOWN", "WASTE"] = "UNKNOWN"
    resource_release_status: Literal["UNCONFIRMED", "CONFIRMED"] = "UNCONFIRMED"
    remaining_sec: NonNegativeInt | None = None
    recovery_rule_id: NonEmpty | None = None
    resource_ids: tuple[NonEmpty, ...] = ()


class DevicePayload(FrozenModel):
    device_id: NonEmpty
    execution_id: ExecutionId | None = None
    reason: NonEmpty | None = None
    expected_recovery_at: AwareDatetime | None = None
    recovery_estimate_source: NonEmpty | None = None
    configuration: tuple[ConfigurationValue, ...] = ()


class MaterialPayload(FrozenModel):
    lot_id: MaterialLotId
    before: ScaledQuantity
    after: ScaledQuantity
    reason: NonEmpty
    evidence_refs: tuple[NonEmpty, ...]


class OverridePayload(FrozenModel):
    correction_type: Literal["PLAN_PREFERENCE", "REMAINING_DURATION"]
    target_id: NonEmpty
    before_value: NonNegativeInt
    after_value: NonNegativeInt
    reason: NonEmpty
    operator: NonEmpty
    evidence_refs: tuple[NonEmpty, ...]


class SimulationPayload(FrozenModel):
    advance_sec: NonNegativeInt


class SessionPayload(FrozenModel):
    reason: NonEmpty


class ReplanPayload(FrozenModel):
    reason: NonEmpty = "请求重排剩余操作"


class RuntimeEvent(FrozenModel):
    event_id: EventId
    session_id: SessionId
    event_type: EventType
    occurred_at: AwareDatetime
    received_at: AwareDatetime
    source: EventSource
    expected_state_revision: NonNegativeInt
    base_plan_version: NonNegativeInt
    payload: (
        MenuPayload
        | ExecutionPayload
        | DevicePayload
        | MaterialPayload
        | OverridePayload
        | SimulationPayload
        | SessionPayload
        | ReplanPayload
    )

    @model_validator(mode="before")
    @classmethod
    def replan_payload(cls, value: object) -> object:
        if (
            isinstance(value, dict)
            and value.get("event_type") == EventType.REPLAN_REQUESTED
            and isinstance(value.get("payload"), dict)
        ):
            return {**value, "payload": ReplanPayload.model_validate(value["payload"])}
        return value

    @model_validator(mode="after")
    def payload_matches_event(self) -> Self:
        kind, payload = self.event_type, self.payload
        if kind in (
            EventType.START_SESSION,
            EventType.ADD_RECIPE,
            EventType.CANCEL_RECIPE,
            EventType.DELAY_RECIPE,
        ):
            if not isinstance(payload, MenuPayload):
                raise ValueError("菜单事件载荷不匹配")
            if kind in (EventType.START_SESSION, EventType.ADD_RECIPE) and not payload.recipes:
                raise ValueError("加菜必须包含菜谱")
            if (
                kind in (EventType.CANCEL_RECIPE, EventType.DELAY_RECIPE)
                and payload.recipe_instance_id is None
            ):
                raise ValueError("菜单变更必须指定实例")
            if kind == EventType.DELAY_RECIPE and payload.earliest_start_sec is None:
                raise ValueError("延迟必须包含时间")
        elif kind.value.startswith("DEVICE_"):
            if not isinstance(payload, DevicePayload):
                raise ValueError("设备事件载荷不匹配")
            if kind == EventType.DEVICE_RELEASE_CONFIRMED and payload.execution_id is None:
                raise ValueError("释放确认必须对应具体执行")
            if kind == EventType.DEVICE_UNAVAILABLE and payload.reason is None:
                raise ValueError("故障必须包含原因")
            if payload.expected_recovery_at is not None and not payload.recovery_estimate_source:
                raise ValueError("恢复估计必须有来源")
        elif kind.value.startswith("OPERATION_") or kind == EventType.DURATION_UPDATED:
            if not isinstance(payload, ExecutionPayload):
                raise ValueError("执行事件载荷不匹配")
            if kind == EventType.OPERATION_FAILED and payload.reason is None:
                raise ValueError("执行失败必须包含原因")
            if kind == EventType.OPERATION_RETRY_REQUESTED and payload.recovery_rule_id is None:
                raise ValueError("重做必须引用审核恢复规则")
            if kind == EventType.DURATION_UPDATED and payload.remaining_sec is None:
                raise ValueError("更新时长必须明确剩余秒数")
        elif kind.value.startswith("MATERIAL_"):
            if not isinstance(payload, MaterialPayload):
                raise ValueError("物料事件载荷不匹配")
        elif kind == EventType.MANUAL_OVERRIDE and not isinstance(payload, OverridePayload):
            raise ValueError("人工修正必须采用白名单")
        elif kind == EventType.ADVANCE_SIMULATION:
            if not isinstance(payload, SimulationPayload) or self.source != EventSource.SIMULATED:
                raise ValueError("模拟推进必须明确标注模拟来源")
        elif kind == EventType.RESET_SESSION and not isinstance(payload, SessionPayload):
            raise ValueError("会话重置必须有原因")
        elif kind == EventType.REPLAN_REQUESTED and not isinstance(
            payload, (ReplanPayload, SessionPayload)
        ):
            raise ValueError("重排命令载荷不匹配")
        return self


class EventApplyResult(FrozenModel):
    event_id: EventId
    first_applied: bool
    status: Literal["APPLIED", "REJECTED", "CONFLICT"]
    state_revision: NonNegativeInt
    fact_change_refs: tuple[NonEmpty, ...] = ()
    requires_replan: bool = False
    rejection_reason: NonEmpty | None = None
    replan_reasons: tuple[NonEmpty, ...] = Field(default=(), exclude_if=lambda v: not v)

    @model_validator(mode="after")
    def rejected(self) -> Self:
        if self.status != "APPLIED" and (
            self.first_applied
            or self.requires_replan
            or self.fact_change_refs
            or not self.rejection_reason
        ):
            raise ValueError("拒绝事件不应声明事实已生效，并须有原因")
        return self
