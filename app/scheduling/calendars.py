"""完整状态快照实现原子撤销；基线冻结事实永远不可撤销。"""

from app.domain.base import content_hash
from app.domain.resources import ResourceUse
from app.domain.scheduling_problem import SchedulingProblem
from app.scheduling.calendar_projection import entries_for, fixed_ports
from app.scheduling.calendar_types import (
    CalendarEntry,
    CalendarSnapshot,
    MaterialAllocation,
    PlacementResult,
    VirtualOutput,
)


class CalendarState:
    def __init__(self, problem: SchedulingProblem) -> None:
        ports = fixed_ports(problem)
        entries = list(entries_for(problem, (), include_fixed=True))
        for block in problem.resource_blocks:
            entries.append(
                CalendarEntry(
                    physical_key=(block.physical_resource_id, block.component_id),
                    interval=block.interval,
                    task_ids=(),
                    frozen=True,
                    use=block.occupancy_use
                    or ResourceUse(
                        resource_type="DEVICE",
                        resource_id=block.resource_id,
                        physical_resource_id=block.physical_resource_id,
                        component_id=block.component_id,
                        conflict_policy="UNARY",
                    ),
                )
            )
        self.current = CalendarSnapshot(
            entries=tuple(entries), ports=ports, covered=tuple(p.task_id for p in ports)
        )
        self.history: list[CalendarSnapshot] = []

    @property
    def state_hash(self) -> str:
        return content_hash(self.current)

    @property
    def material_allocations(self) -> tuple[MaterialAllocation, ...]:
        return self.current.material_allocations

    @property
    def virtual_outputs(self) -> tuple[VirtualOutput, ...]:
        return self.current.virtual_outputs

    def commit(self, placement: PlacementResult) -> None:
        if not placement.assignments or placement.rejection_reasons:
            raise ValueError("不能提交失败或空的插入")
        new_ids = tuple(t for a in placement.assignments for t in a.task_ids)
        if len(set(new_ids)) != len(new_ids) or set(new_ids) & set(self.current.covered):
            raise ValueError("插入重复覆盖或覆盖冻结事实")
        self.history.append(self.current)
        self.current = CalendarSnapshot(
            entries=self.current.entries + placement.entries,
            assignments=self.current.assignments + placement.assignments,
            ports=self.current.ports + placement.logical_time_mapping,
            material_allocations=self.current.material_allocations + placement.material_allocations,
            virtual_outputs=self.current.virtual_outputs + placement.virtual_outputs,
            covered=self.current.covered + new_ids,
        )

    def rollback(self) -> None:
        if not self.history:
            raise ValueError("冻结基线不可撤销")
        self.current = self.history.pop()
