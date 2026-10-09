"""菜内强制工艺的实例级预约、热暴露和时间端口。"""

from typing import Literal

from app.domain.base import FrozenModel, NonEmpty, NonNegativeInt, PositiveInt
from app.domain.ids import RecipeInstanceId, TaskId
from app.domain.resources import ConflictPolicy


class TaskTimeRelation(FrozenModel):
    left_task: TaskId
    left_anchor: Literal["START", "END"]
    right_task: TaskId
    right_anchor: Literal["START", "END"]
    min_offset_sec: int = 0
    max_offset_sec: int | None = None
    evidence_refs: tuple[NonEmpty, ...]


class TaskReservation(FrozenModel):
    reservation_id: NonEmpty
    recipe_instance_id: RecipeInstanceId
    members: tuple[TaskId, ...]
    resource_options: tuple[NonEmpty, ...]
    conflict_policy: ConflictPolicy
    evidence_refs: tuple[NonEmpty, ...]


class FixedTaskBatch(FrozenModel):
    batch_id: NonEmpty
    recipe_instance_id: RecipeInstanceId
    members: tuple[TaskId, ...]
    evidence_refs: tuple[NonEmpty, ...]


class TaskProgram(FrozenModel):
    program_id: NonEmpty
    recipe_instance_id: RecipeInstanceId
    preparation_members: tuple[TaskId, ...]
    before_members: tuple[TaskId, ...]
    intervention_members: tuple[TaskId, ...]
    after_members: tuple[TaskId, ...]
    active_process_sec: PositiveInt
    before_intervention_sec: NonNegativeInt
    intervention_sec: NonNegativeInt
    remaining_sec: NonNegativeInt
    timer_paused: bool
    evidence_refs: tuple[NonEmpty, ...]


class ExpandedPrograms(FrozenModel):
    reservations: tuple[TaskReservation, ...] = ()
    fixed_batches: tuple[FixedTaskBatch, ...] = ()
    programs: tuple[TaskProgram, ...] = ()
    time_relations: tuple[TaskTimeRelation, ...] = ()
