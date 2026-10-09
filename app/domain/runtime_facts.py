"""P4 事实扩展；空扩展不改变旧知识和问题的哈希。"""

from typing import Literal

from pydantic import AwareDatetime, Field

from app.domain.advance_preparation import AdvancePreparation
from app.domain.amount import RationalAmount as RationalAmount
from app.domain.base import FrozenModel, NonEmpty, NonNegativeInt
from app.domain.ids import ExecutionId, TaskId
from app.domain.inventory import InventoryFulfillment, InventoryRule
from app.domain.material import MaterialSpec
from app.domain.quantity import ScaledQuantity, Unit
from app.domain.resources import ResourceUse
from app.domain.time import Interval


class TaskSpan(FrozenModel):
    task_id: TaskId
    interval: Interval


class ActualResourceSpan(FrozenModel):
    resource: ResourceUse
    interval: Interval
    event_refs: tuple[NonEmpty, ...]


class Occupancy(FrozenModel):
    occupancy_id: NonEmpty
    execution_id: ExecutionId
    resource: ResourceUse
    started_at: AwareDatetime
    released_at: AwareDatetime | None = None
    release_event_id: NonEmpty | None = None
    awaiting_confirmation: bool = False
    reservation_id: NonEmpty | None = Field(default=None, exclude_if=lambda value: value is None)
    reservation_members: tuple[TaskId, ...] = Field(default=(), exclude_if=lambda value: not value)


class LotFact(FrozenModel):
    lot_id: NonEmpty
    spec_id: NonEmpty
    source_spec_id: NonEmpty
    quantity_kind: Literal["EXACT", "QUALITATIVE", "RECIPE_BATCH"]
    unit: Unit | None = None
    produced: RationalAmount
    available: RationalAmount
    reserved: RationalAmount = RationalAmount(numerator=0)
    quality_status: Literal["QUALIFIED", "UNKNOWN", "WASTE"]
    produced_at: AwareDatetime
    source_event_id: NonEmpty
    produced_by_execution_id: ExecutionId | None = None
    expires_at: AwareDatetime | None = None
    availability_evidence: tuple[NonEmpty, ...] = ()
    storage_state: NonEmpty = "declared"
    material_spec: MaterialSpec | None = Field(default=None, exclude_if=lambda value: value is None)
    preparation_id: NonEmpty | None = Field(default=None, exclude_if=lambda value: value is None)

    def exact(self, amount: RationalAmount) -> ScaledQuantity:
        if self.quantity_kind != "EXACT" or self.unit is None:
            raise ValueError("定性整批不可换算成克数")
        return ScaledQuantity(value=amount.numerator, scale=amount.denominator, unit=self.unit)


class RuntimeDetails(FrozenModel):
    schema_version: Literal["p4-runtime-v1"] = "p4-runtime-v1"
    planning_kind: Literal["INITIAL", "REPLAN"] = "INITIAL"
    occupancies: tuple[Occupancy, ...] = ()
    lots: tuple[LotFact, ...] = ()
    cancelled_instance_ids: tuple[NonEmpty, ...] = ()
    earliest_starts: tuple[tuple[NonEmpty, NonNegativeInt], ...] = ()
    blocked_task_ids: tuple[TaskId, ...] = ()
    retired_task_ids: tuple[TaskId, ...] = ()
    inventory_rules: tuple[InventoryRule, ...] = Field(
        default=(), exclude_if=lambda value: not value
    )
    inventory_fulfillments: tuple[InventoryFulfillment, ...] = Field(
        default=(), exclude_if=lambda value: not value
    )
    advance_preparations: tuple[AdvancePreparation, ...] = Field(
        default=(), exclude_if=lambda value: not value
    )
