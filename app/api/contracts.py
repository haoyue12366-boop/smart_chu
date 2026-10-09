"""展示端协议；完整时间、事件接受和计划发布分别表达。"""

from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from app.domain.base import FrozenModel, NonEmpty, NonNegativeInt
from app.domain.events import (
    DevicePayload,
    EventRecipe,
    EventSource,
    EventType,
    ExecutionPayload,
    MaterialPayload,
    MenuPayload,
    OverridePayload,
    ReplanPayload,
    SessionPayload,
    SimulationPayload,
)


class CreateSessionRequest(FrozenModel):
    recipes: tuple[EventRecipe, ...] = Field(min_length=1, max_length=100)
    event_id: NonEmpty
    mode: Literal["SCHEDULE_CLOCK", "MANUAL_CONFIRM", "SIMULATED"] = "SCHEDULE_CLOCK"


class ReplanRequest(SessionPayload):
    reason: NonEmpty = "请求重排剩余操作"


class EventRequest(FrozenModel):
    event_id: NonEmpty
    event_type: EventType
    expected_state_revision: NonNegativeInt
    base_plan_version: NonNegativeInt
    occurred_at: AwareDatetime | None = None
    source: EventSource = EventSource.MANUAL_CONFIRM
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
    def select_payload(cls, value: object) -> object:
        if (
            isinstance(value, dict)
            and value.get("event_type") == EventType.REPLAN_REQUESTED
            and isinstance(value.get("payload"), dict)
        ):
            return {**value, "payload": ReplanPayload.model_validate(value["payload"])}
        return value
