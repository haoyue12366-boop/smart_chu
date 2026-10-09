"""同腔体保持相同配置，各层独立预约，实际层位随候选和执行事实保存。"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from app.domain.ids import TaskId
from app.domain.schedule import CandidateSchedule
from app.domain.scheduling_problem import SchedulingProblem
from app.domain.time import Interval
from app.scheduling.calendar_projection import entries_for

if TYPE_CHECKING:
    from ortools.sat.python import cp_model

    from app.scheduling.model_builder import ModelBuilder
    from app.scheduling.resource_model import ResourceInterval


def add_layer_constraints(builder: ModelBuilder, occupancies: list[ResourceInterval]) -> None:
    model, problem = builder.model, builder.problem
    devices = {d.competition_key: d for d in problem.resources if d.capacity > 1}
    for key, device in devices.items():
        layers: list[list[cp_model.IntervalVar]] = [[] for _ in range(device.capacity)]
        before = len(model.proto.constraints)
        for index, occurrence in enumerate(occupancies):
            if occurrence.key != key:
                continue
            builder.check_budget()
            fixed_layers = occurrence.use.occupied_layer_indices
            if fixed_layers:
                if any(number > device.capacity for number in fixed_layers):
                    raise ValueError("固定占用层位超出已确认设备层数")
                for number in fixed_layers:
                    layers[number - 1].append(occurrence.interval)
                continue
            if occurrence.use.units != 1:
                raise ValueError("分层载体必须为每个实际托盘保留独立预约")
            layer = model.new_int_var(1, device.capacity, f"layer:{key}:{index}")
            if occurrence.use.layer_index is not None:
                model.add(layer == occurrence.use.layer_index)
            flags = []
            size = model.new_int_var(0, problem.horizon_sec, f"layer-size:{key}:{index}")
            model.add(size == occurrence.end - occurrence.start)
            for number in range(1, device.capacity + 1):
                present = model.new_bool_var(f"layer-present:{key}:{index}:{number}")
                model.add(layer == number).only_enforce_if(present)
                flags.append(present)
                layers[number - 1].append(
                    model.new_optional_interval_var(
                        occurrence.start,
                        size,
                        occurrence.end,
                        present,
                        f"layer-interval:{key}:{index}:{number}",
                    )
                )
            model.add(sum(flags) == occurrence.presence)
            builder.layer_choices.append((occurrence, layer))
        for block in problem.resource_blocks:
            if (block.physical_resource_id, block.component_id) != key:
                continue
            use = block.occupancy_use
            numbers = (
                use.effective_layer_indices
                if use is not None and use.effective_layer_indices
                else range(1, device.capacity + 1)
            )
            interval = model.new_fixed_size_interval_var(
                block.interval.start_sec,
                block.interval.end_sec - block.interval.start_sec,
                f"layer-block:{block.resource_id}:{block.interval.start_sec}",
            )
            for number in numbers:
                if not 1 <= number <= device.capacity:
                    raise ValueError("历史占用层位超出已确认设备层数")
                layers[number - 1].append(interval)
        for intervals in layers:
            if intervals:
                model.add_no_overlap(intervals)
        builder.mark("RESOURCE", before, resources=(device.device_instance_id,))


def allocate_layers(candidate: CandidateSchedule, problem: SchedulingProblem) -> CandidateSchedule:
    """为 Greedy 完整候选分配真实空闲层；Solver 的显式层位和冻结事实保持不变。"""
    devices = {d.competition_key: d for d in problem.resources if d.capacity > 1}
    if not devices:
        return candidate
    entries = entries_for(problem, candidate.assignments, include_fixed=True)
    assigned: dict[tuple[TaskId, str], int] = {}
    for key, device in devices.items():
        uses = sorted(
            (entry for entry in entries if entry.physical_key == key),
            key=lambda entry: (entry.interval.start_sec, entry.interval.end_sec),
        )
        occupied: dict[int, list[Interval]] = defaultdict(list)
        for block in problem.resource_blocks:
            if (block.physical_resource_id, block.component_id) != key:
                continue
            use = block.occupancy_use
            numbers = (
                use.effective_layer_indices
                if use is not None and use.effective_layer_indices
                else range(1, device.capacity + 1)
            )
            for occupied_layer in numbers:
                if not 1 <= occupied_layer <= device.capacity:
                    raise ValueError("历史占用层位超出已确认设备层数")
                occupied[occupied_layer].append(block.interval)
        for entry in uses:
            if entry.use.effective_layer_indices:
                for fixed_number in entry.use.effective_layer_indices:
                    if not 1 <= fixed_number <= device.capacity:
                        raise ValueError("计划层位超出已确认设备层数")
                    occupied[fixed_number].append(entry.interval)
            elif entry.frozen:
                raise ValueError("多层设备的冻结占用缺少层位")
        for entry in uses:
            if entry.use.occupied_layer_indices:
                continue
            number = entry.use.layer_index
            if number is None:
                number = next(
                    (
                        index
                        for index in range(1, device.capacity + 1)
                        if not any(entry.interval.overlaps(span) for span in occupied[index])
                    ),
                    None,
                )
                if number is None:
                    raise ValueError("设备所有层位已占用，不能发布额外托盘")
                occupied[number].append(entry.interval)
            if not 1 <= number <= device.capacity:
                raise ValueError("计划层位超出已确认设备层数")
            assigned.update(((task, entry.use.resource_id), number) for task in entry.task_ids)
    assignments = tuple(
        assignment.model_copy(
            update={
                "resource_uses": tuple(
                    use.model_copy(
                        update={"layer_index": assigned[assignment.task_ids[0], use.resource_id]}
                    )
                    if (assignment.task_ids[0], use.resource_id) in assigned
                    else use
                    for use in assignment.resource_uses
                )
            }
        )
        for assignment in candidate.assignments
    )
    return candidate.model_copy(update={"assignments": assignments})
