"""实例隔离的物料供需图；有理份额不使用浮点或无限库存。"""

from typing import Literal

from pydantic import Field

from app.domain.base import FrozenModel, NonEmpty, NonNegativeInt, PositiveInt
from app.domain.ids import CarrierId, MaterialLotId, RecipeInstanceId, TaskId
from app.domain.material import MaterialRequirement


class MaterialSupply(FrozenModel):
    supply_id: NonEmpty
    recipe_instance_id: RecipeInstanceId
    source_spec_id: NonEmpty
    requirement: MaterialRequirement
    producer_task_id: TaskId | None = None
    actual_lot_ids: tuple[MaterialLotId, ...] = ()
    declared_lot_ids: tuple[MaterialLotId, ...] = Field(
        default=(), exclude_if=lambda value: not value
    )
    preparation_id: NonEmpty | None = Field(default=None, exclude_if=lambda value: value is None)
    available_at_sec: NonNegativeInt = 0
    available_share_numerator: NonNegativeInt = 1
    available_share_denominator: PositiveInt = 1


class MaterialDemand(FrozenModel):
    demand_id: NonEmpty
    task_id: TaskId
    supply_id: NonEmpty
    requirement: MaterialRequirement
    share_numerator: PositiveInt
    share_denominator: PositiveInt
    is_loss: bool = False


class MaterialFlow(FrozenModel):
    supplies: tuple[MaterialSupply, ...] = ()
    demands: tuple[MaterialDemand, ...] = ()


class PlannedMaterialAllocation(FrozenModel):
    producer_carrier_id: CarrierId
    supply_id: NonEmpty
    demand_id: NonEmpty
    consumer_task_id: TaskId
    share_numerator: PositiveInt
    share_denominator: PositiveInt
    is_loss: bool = False


class PlannedMaterialRemainder(FrozenModel):
    producer_carrier_id: CarrierId
    supply_id: NonEmpty
    share_numerator: NonNegativeInt
    share_denominator: PositiveInt
    is_actual_inventory: Literal[False] = False


class MaterialAllocationModel(FrozenModel):
    allocations: tuple[PlannedMaterialAllocation, ...] = ()
    remainders: tuple[PlannedMaterialRemainder, ...] = ()
