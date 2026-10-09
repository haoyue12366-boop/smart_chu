"""按物理资源建立外层预约；允许共享资源只禁止不兼容重叠。"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.domain.ids import CarrierId, TaskId
from app.domain.resources import ResourceUse
from app.domain.runtime_history import unmodeled_human_history

if TYPE_CHECKING:
    from ortools.sat.python import cp_model

    from app.scheduling.model_builder import ModelBuilder


@dataclass
class ResourceInterval:
    key: tuple[str, str]
    start: cp_model.IntVar
    end: cp_model.IntVar
    interval: cp_model.IntervalVar
    presence: cp_model.IntVar
    use: ResourceUse
    tasks: tuple[TaskId, ...]
    replaces_reservations: bool = False
    carrier_id: CarrierId | None = None
    start_offset_sec: int = 0
    end_offset_sec: int | None = None


def add_resource_constraints(builder: ModelBuilder) -> None:
    problem, model = builder.problem, builder.model
    devices = {d.device_instance_id: d for d in problem.resources}
    uses: list[ResourceInterval] = []
    always = model.new_constant(1)
    for index, span in enumerate(unmodeled_human_history(problem)):
        start = model.new_constant(span.interval.start_sec)
        end = model.new_constant(span.interval.end_sec)
        uses.append(
            ResourceInterval(
                ("human_1", "human_1"),
                start,
                end,
                model.new_interval_var(
                    start,
                    span.interval.end_sec - span.interval.start_sec,
                    end,
                    f"past-human:{index}",
                ),
                always,
                span.resource,
                (),
            )
        )
    for carrier in builder.candidates:
        phases = [(u, 0, carrier.duration_sec) for u in carrier.resource_uses]
        phases.extend(
            (p.resource_use, p.start_offset_sec, p.end_offset_sec) for p in carrier.resource_phases
        )
        for i, (use, offset_start, offset_end) in enumerate(phases):
            builder.check_budget()
            if use.resource_type == "HUMAN":
                if use.resource_id != "human_1" or use.units != 1:
                    raise ValueError("非法人工资源")
                key = ("human_1", "human_1")
            else:
                device = devices.get(use.resource_id)
                if device is None or device.competition_key != (
                    use.physical_resource_id,
                    use.component_id,
                ):
                    raise ValueError("设备缺少一致的物理映射")
                key = device.competition_key
            start = builder.carrier_starts[carrier.carrier_id]
            end = builder.carrier_ends[carrier.carrier_id]
            if offset_start or offset_end != carrier.duration_sec:
                phase_start = model.new_int_var(
                    0, problem.horizon_sec, f"{carrier.carrier_id.root}:phase:{i}:start"
                )
                phase_end = model.new_int_var(
                    0, problem.horizon_sec, f"{carrier.carrier_id.root}:phase:{i}:end"
                )
                model.add(phase_start == start + offset_start).only_enforce_if(
                    builder.selected[carrier.carrier_id]
                )
                model.add(phase_end == start + offset_end).only_enforce_if(
                    builder.selected[carrier.carrier_id]
                )
                start, end = phase_start, phase_end
            interval = model.new_optional_interval_var(
                start,
                offset_end - offset_start,
                end,
                builder.selected[carrier.carrier_id],
                f"{carrier.carrier_id.root}:resource:{i}",
            )
            uses.append(
                ResourceInterval(
                    key,
                    start,
                    end,
                    interval,
                    builder.selected[carrier.carrier_id],
                    use,
                    carrier.covers,
                    bool(carrier.replaced_reservation_ids),
                    carrier.carrier_id,
                    offset_start,
                    offset_end,
                )
            )
    task_map = {t.task_id: t for t in problem.logical_tasks}
    for execution in problem.fixed_executions:
        explicit = (
            execution.scheduled_resource_spans
            if execution.status == "RUNNING"
            else execution.resource_spans
        )
        if explicit:
            for i, span in enumerate(explicit):
                use = span.resource
                key = (
                    ("human_1", "human_1")
                    if use.resource_type == "HUMAN"
                    else devices[use.resource_id].competition_key
                )
                start, end = (
                    model.new_constant(span.interval.start_sec),
                    model.new_constant(span.interval.end_sec),
                )
                uses.append(
                    ResourceInterval(
                        key,
                        start,
                        end,
                        model.new_interval_var(
                            start,
                            span.interval.end_sec - span.interval.start_sec,
                            end,
                            f"actual:{execution.execution_id.root}:{i}",
                        ),
                        always,
                        use,
                        execution.task_ids,
                        len({p.interval for p in execution.task_spans}) > 1,
                    )
                )
            continue
        for task in execution.task_ids:
            if task not in task_map:
                continue
            for i, required in enumerate(task_map[task].operation.resource_requirements):
                if required.resource_type == "HUMAN":
                    key, use = ("human_1", "human_1"), required
                else:
                    choices = [
                        d
                        for rid in execution.resource_ids
                        if (d := devices.get(rid)) is not None
                        and d.physical_resource_id == required.physical_resource_id
                        and (
                            d.component_id == required.component_id
                            or required.resource_id not in devices
                        )
                    ]
                    if len(choices) != 1:
                        raise ValueError("冻结设备占用缺少唯一物理实例")
                    device = choices[0]
                    key = device.competition_key
                    use = required.model_copy(
                        update={
                            "resource_id": device.device_instance_id,
                            "physical_resource_id": device.physical_resource_id,
                            "component_id": device.component_id,
                        }
                    )
                fixed_start, fixed_end = builder.fixed[task]
                interval = model.new_interval_var(
                    builder.starts[task],
                    fixed_end - fixed_start,
                    builder.ends[task],
                    f"{execution.execution_id.root}:{task.root}:{i}",
                )
                uses.append(
                    ResourceInterval(
                        key,
                        builder.starts[task],
                        builder.ends[task],
                        interval,
                        always,
                        use,
                        (task,),
                    )
                )
    builder.human_intervals = [u for u in uses if u.use.resource_type == "HUMAN"]
    hidden: set[int] = set()
    outer = []
    for reservation in problem.mandatory_programs.reservations:
        if any(
            set(reservation.members) <= set(e.task_ids)
            and len({p.interval for p in e.task_spans}) > 1
            for e in problem.fixed_executions
        ):
            continue
        builder.check_budget()
        before = len(model.proto.constraints)
        keys = sorted(
            {devices[r].competition_key for r in reservation.resource_options if r in devices}
        )
        start = model.new_int_var(0, problem.horizon_sec, reservation.reservation_id + ":start")
        end = model.new_int_var(0, problem.horizon_sec, reservation.reservation_id + ":end")
        size = model.new_int_var(0, problem.horizon_sec, reservation.reservation_id + ":duration")
        model.add_min_equality(start, [builder.starts[t] for t in reservation.members])
        model.add_max_equality(end, [builder.ends[t] for t in reservation.members])
        model.add(size == end - start)
        flags = []
        for key in keys:
            present = model.new_bool_var(reservation.reservation_id + ":" + ":".join(key))
            flags.append(present)
            selected_uses = [
                (i, u)
                for i, u in enumerate(uses)
                if u.key == key
                and set(u.tasks) & set(reservation.members)
                and not u.replaces_reservations
            ]
            for task in reservation.members:
                matching = {
                    u.presence.index: u.presence for _, u in selected_uses if task in u.tasks
                }
                model.add(sum(matching.values()) == present)
            hidden.update(i for i, _ in selected_uses)
            if selected_uses:
                frozen_layers = {
                    occurrence.use.effective_layer_indices
                    for _, occurrence in selected_uses
                    if occurrence.use.effective_layer_indices
                    and any(task in builder.fixed for task in occurrence.tasks)
                }
                if len(frozen_layers) > 1:
                    raise ValueError("同一连续设备预约的实际层位不一致")
                declared = selected_uses[0][1].use
                frozen = next(iter(frozen_layers), ())
                if (
                    frozen
                    and declared.occupied_layer_indices
                    and frozen != declared.occupied_layer_indices
                ):
                    raise ValueError("冻结层位与固定工艺占用集合不同")
                outer_use = declared.model_copy(
                    update={
                        "conflict_policy": reservation.conflict_policy,
                        "layer_index": (
                            frozen[0]
                            if frozen and not declared.occupied_layer_indices
                            else declared.layer_index
                        ),
                    }
                )
                interval = model.new_optional_interval_var(
                    start, size, end, present, reservation.reservation_id + ":outer"
                )
                outer.append(
                    ResourceInterval(
                        key,
                        start,
                        end,
                        interval,
                        present,
                        outer_use,
                        reservation.members,
                    )
                )
        replacements = [
            builder.selected[c.carrier_id]
            for c in problem.thermal_batch_candidates
            if reservation.reservation_id in c.replaced_reservation_ids
        ]
        model.add_exactly_one([*flags, *replacements])
        builder.mark("RESERVATION", before, reservation.members)
    effective = [use for i, use in enumerate(uses) if i not in hidden] + outer
    from app.scheduling.layer_resources import add_layer_constraints
    from app.scheduling.thermal_constraints import add_thermal_sequences

    add_layer_constraints(builder, effective)
    add_thermal_sequences(builder, effective)
    grouped: dict[tuple[str, str], list[ResourceInterval]] = defaultdict(list)
    for resource_interval in effective:
        grouped[resource_interval.key].append(resource_interval)
    for key, values in grouped.items():
        builder.check_budget()
        before = len(model.proto.constraints)
        if all(u.use.conflict_policy in {"UNARY", "BATCH_EXCLUSIVE"} for u in values):
            model.add_no_overlap([u.interval for u in values])
        else:
            for i, left in enumerate(values):
                for right in values[i + 1 :]:
                    builder.check_budget()
                    a = {
                        c.parameter: c.value
                        for c in left.use.configuration
                        if c.parameter != "duration_sec"
                    }
                    b = {
                        c.parameter: c.value
                        for c in right.use.configuration
                        if c.parameter != "duration_sec"
                    }
                    layered = any(
                        d.capacity > 1 and d.competition_key == key for d in problem.resources
                    )
                    conflict = (
                        not {"temperature_c", "mode"} <= a.keys() or a != b
                        if layered
                        else any(a[p] != b[p] for p in a.keys() & b.keys())
                    )
                    if (
                        conflict
                        or left.use.conflict_policy in {None, "UNARY", "BATCH_EXCLUSIVE"}
                        or right.use.conflict_policy in {None, "UNARY", "BATCH_EXCLUSIVE"}
                    ):
                        order = model.new_bool_var(f"resource-order:{key}:{builder.sequence_arcs}")
                        builder.sequence_arcs += 1
                        model.add(right.start >= left.end).only_enforce_if(
                            [left.presence, right.presence, order]
                        )
                        model.add(left.start >= right.end).only_enforce_if(
                            [left.presence, right.presence, order.negated()]
                        )
        builder.mark("RESOURCE", before, resources=tuple(u.use.resource_id for u in values))
        for block in problem.resource_blocks:
            if (block.physical_resource_id, block.component_id) != key:
                continue
            before = len(model.proto.constraints)
            interval = model.new_fixed_size_interval_var(
                block.interval.start_sec,
                block.interval.end_sec - block.interval.start_sec,
                "blocked:" + block.resource_id,
            )
            for resource_interval in values:
                actual = block.occupancy_use
                if (
                    actual is not None
                    and actual.conflict_policy not in {None, "UNARY", "BATCH_EXCLUSIVE"}
                    and resource_interval.use.conflict_policy
                    not in {None, "UNARY", "BATCH_EXCLUSIVE"}
                ):
                    actual_configuration = {
                        c.parameter: c.value
                        for c in actual.configuration
                        if c.parameter != "duration_sec"
                    }
                    planned_configuration = {
                        c.parameter: c.value
                        for c in resource_interval.use.configuration
                        if c.parameter != "duration_sec"
                    }
                    layered = any(
                        d.capacity > 1 and d.competition_key == key for d in problem.resources
                    )
                    compatible = (
                        {"temperature_c", "mode"} <= actual_configuration.keys()
                        and actual_configuration == planned_configuration
                        if layered
                        else all(
                            actual_configuration[p] == planned_configuration[p]
                            for p in actual_configuration.keys() & planned_configuration.keys()
                        )
                    )
                    if compatible:
                        continue
                model.add_no_overlap([interval, resource_interval.interval])
            builder.mark("RESOURCE_BLOCK", before, resources=(block.resource_id,))
