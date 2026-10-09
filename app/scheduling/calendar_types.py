"""Greedy 试排中的完整事务状态，时间仍为整数秒。"""

from app.domain.base import FrozenModel, NonEmpty
from app.domain.ids import CarrierId, TaskId
from app.domain.resources import ResourceUse
from app.domain.schedule import ScheduledAssignment
from app.domain.time import Interval


class CalendarEntry(FrozenModel):
    physical_key: tuple[str, str]
    interval: Interval
    use: ResourceUse
    task_ids: tuple[TaskId, ...]
    reservation_id: str | None = None
    carrier_id: CarrierId | None = None
    frozen: bool = False


class TaskPort(FrozenModel):
    task_id: TaskId
    interval: Interval


class MaterialAllocation(FrozenModel):
    demand_id: NonEmpty
    supply_id: NonEmpty
    task_id: TaskId


class VirtualOutput(FrozenModel):
    supply_id: NonEmpty
    available_at_sec: int


class PlacementResult(FrozenModel):
    assignments: tuple[ScheduledAssignment, ...] = ()
    entries: tuple[CalendarEntry, ...] = ()
    logical_time_mapping: tuple[TaskPort, ...] = ()
    material_allocations: tuple[MaterialAllocation, ...] = ()
    virtual_outputs: tuple[VirtualOutput, ...] = ()
    rejection_reasons: tuple[NonEmpty, ...] = ()
    gap_checks: int = 0


class CalendarSnapshot(FrozenModel):
    entries: tuple[CalendarEntry, ...] = ()
    assignments: tuple[ScheduledAssignment, ...] = ()
    ports: tuple[TaskPort, ...] = ()
    material_allocations: tuple[MaterialAllocation, ...] = ()
    virtual_outputs: tuple[VirtualOutput, ...] = ()
    covered: tuple[TaskId, ...] = ()
