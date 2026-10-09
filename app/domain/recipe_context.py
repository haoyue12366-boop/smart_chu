"""固定菜内程序和设备外层预约；模板事实不等于本次排程。"""

from typing import Literal

from pydantic import Field

from app.domain.base import FrozenModel, NonEmpty, NonNegativeInt, PositiveInt
from app.domain.cooking_completion import CookingCompletionRule
from app.domain.ids import OperationId, RecipeId
from app.domain.provenance import ProvenanceOrigin
from app.domain.resources import ConflictPolicy


class SourceOperationGroup(FrozenModel):
    source_step: NonEmpty
    operation_ids: tuple[OperationId, ...]


class RecipeReservation(FrozenModel):
    reservation_id: NonEmpty
    members: tuple[OperationId, ...]
    resource_options: tuple[NonEmpty, ...]
    policy: ConflictPolicy
    span: Literal["min_start_to_max_end"]
    origin: ProvenanceOrigin


class RecipeProgram(FrozenModel):
    program_id: NonEmpty
    before_group: tuple[OperationId, ...]
    intervention_group: tuple[OperationId, ...]
    after_group: tuple[OperationId, ...]
    active_process_sec: PositiveInt
    before_intervention_sec: NonNegativeInt
    intervention_sec: NonNegativeInt
    remaining_sec: NonNegativeInt
    timer_paused: bool
    trigger_type: Literal["remaining_time", "elapsed_time"]
    trigger_value: NonNegativeInt
    origin: ProvenanceOrigin


class RecipeSchedulingContext(FrozenModel):
    recipe_id: RecipeId
    cooking_completion: CookingCompletionRule | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    source_groups: tuple[SourceOperationGroup, ...] = ()
    resource_reservations: tuple[RecipeReservation, ...] = ()
    program_constraints: tuple[RecipeProgram, ...] = ()
