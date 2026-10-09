"""库存供应满足逻辑需求，独立于实际加工执行身份。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import Field

from app.domain.amount import RationalAmount
from app.domain.base import FrozenModel, NonEmpty, NonNegativeInt
from app.domain.ids import CarrierId, RecipeInstanceId, TaskId
from app.domain.material import MaterialSpec
from app.domain.quantity import ScaledQuantity, Unit

if TYPE_CHECKING:
    from app.domain.runtime_snapshot import RuntimeSnapshot


class InventoryRule(FrozenModel):
    rule_id: NonEmpty
    target_task_ids: tuple[TaskId, ...]
    source_spec_id: NonEmpty  # 实例绑定的源规格，禁止按食材名称串用批次。
    quantity: RationalAmount
    unit: Unit
    evidence_refs: tuple[NonEmpty, ...]
    knowledge_version: NonEmpty
    source_kind: Literal["REVIEWED", "SYNTHETIC"]
    approved: Literal[True]
    max_age_sec: NonNegativeInt | None = Field(default=None, exclude_if=lambda value: value is None)


class InventoryLotClaim(FrozenModel):
    lot_id: NonEmpty
    quantity: ScaledQuantity
    consumed: RationalAmount = Field(
        default=RationalAmount(numerator=0), exclude_if=lambda value: value.numerator == 0
    )


class InventorySupply(FrozenModel):
    rule_id: NonEmpty
    target_supply_id: NonEmpty
    target_spec: MaterialSpec
    quantity: ScaledQuantity
    lot_claims: tuple[InventoryLotClaim, ...]
    available_at_sec: NonNegativeInt
    expires_at_sec: NonNegativeInt | None = None


class InventoryFulfillment(FrozenModel):
    fulfillment_id: NonEmpty
    publication_id: NonEmpty
    plan_version: NonNegativeInt
    carrier_id: CarrierId
    recipe_instance_id: RecipeInstanceId
    task_ids: tuple[TaskId, ...]
    supply: InventorySupply
    satisfied_at_sec: NonNegativeInt
    status: Literal["PLANNED", "COMMITTED", "SUPERSEDED"] = "PLANNED"


def committed_fulfillments(runtime: RuntimeSnapshot) -> tuple[InventoryFulfillment, ...]:
    details = runtime.details
    if details is None:
        return ()
    return tuple(
        item
        for item in details.inventory_fulfillments
        if item.status == "COMMITTED"
        and item.recipe_instance_id.root not in details.cancelled_instance_ids
    )
