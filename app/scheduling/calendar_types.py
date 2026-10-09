"""Greedy 内部不可变事务值；对外候选仍由严格领域契约构造。"""

from dataclasses import dataclass

from app.domain.ids import CarrierId, TaskId
from app.domain.resources import ResourceUse
from app.domain.schedule import ScheduledAssignment
from app.domain.time import Interval


@dataclass(frozen=True, slots=True, kw_only=True)
class CalendarEntry:
    physical_key: tuple[str, str]
    interval: Interval
    use: ResourceUse
    task_ids: tuple[TaskId, ...]
    reservation_id: str | None = None
    carrier_id: CarrierId | None = None
    frozen: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class TaskPort:
    task_id: TaskId
    interval: Interval


@dataclass(frozen=True, slots=True, kw_only=True)
class MaterialAllocation:
    demand_id: str
    supply_id: str
    task_id: TaskId


@dataclass(frozen=True, slots=True, kw_only=True)
class VirtualOutput:
    supply_id: str
    available_at_sec: int


@dataclass(frozen=True, slots=True, kw_only=True)
class PlacementResult:
    assignments: tuple[ScheduledAssignment, ...] = ()
    entries: tuple[CalendarEntry, ...] = ()
    logical_time_mapping: tuple[TaskPort, ...] = ()
    material_allocations: tuple[MaterialAllocation, ...] = ()
    virtual_outputs: tuple[VirtualOutput, ...] = ()
    rejection_reasons: tuple[str, ...] = ()
    gap_checks: int = 0


@dataclass(frozen=True, slots=True, kw_only=True)
class CalendarSnapshot:
    entries: tuple[CalendarEntry, ...] = ()
    assignments: tuple[ScheduledAssignment, ...] = ()
    ports: tuple[TaskPort, ...] = ()
    material_allocations: tuple[MaterialAllocation, ...] = ()
    virtual_outputs: tuple[VirtualOutput, ...] = ()
    covered: tuple[TaskId, ...] = ()
