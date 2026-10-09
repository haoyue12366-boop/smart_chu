"""独立按物理竞争键扫描实际占用和外层预约。"""

from collections import defaultdict
from dataclasses import dataclass

from app.domain.ids import TaskId
from app.domain.knowledge import ReleaseScope
from app.domain.resources import ResourceUse
from app.domain.time import Interval
from app.validation.device_paths import legal_device_options
from app.validation.schedule_context import Scan


@dataclass(frozen=True)
class Occupancy:
    key: tuple[str, str]
    interval: Interval
    use: ResourceUse
    tasks: tuple[TaskId, ...]
    reservation_id: str | None = None


def _resolve_occupancies(scan: Scan) -> list[Occupancy]:
    knowledge = scan.knowledge
    scope = ReleaseScope(
        release_kind="development" if knowledge.release.release_kind == "development" else "sample",
        rule_version=knowledge.release.rule_version,
        devices=knowledge.devices,
        device_choices=knowledge.device_choices,
        evidence_ids=tuple(e.evidence_id for e in knowledge.provenance_index),
    )
    devices = {d.device_instance_id: d for d in knowledge.devices}
    result = []
    shared = {c.carrier_id: c for c in scan.problem.shared_prep_candidates}
    thermal = {c.carrier_id: c for c in scan.problem.thermal_batch_candidates}
    inventory = {c.carrier_id for c in scan.problem.inventory_supply_candidates}
    for assignment in scan.candidate.assignments:
        if assignment.carrier_id in inventory:
            continue
        batch = thermal.get(assignment.carrier_id)
        if batch is not None:
            anchor = assignment.interval.start_sec
            for use in assignment.resource_uses:
                key = (use.physical_resource_id or "unknown", use.component_id or "unknown")
                result.append(Occupancy(key, assignment.interval, use, assignment.task_ids))
            for phase in batch.resource_phases:
                use = phase.resource_use
                key = (
                    ("human_1", "human_1")
                    if use.resource_type == "HUMAN"
                    else (use.physical_resource_id or "unknown", use.component_id or "unknown")
                )
                result.append(
                    Occupancy(
                        key,
                        Interval(
                            start_sec=anchor + phase.start_offset_sec,
                            end_sec=anchor + phase.end_offset_sec,
                        ),
                        use,
                        (phase.task_id,),
                    )
                )
            continue
        if assignment.carrier_id in shared:
            # Resource facts are separately checked against every member in shared_prep.
            for use in assignment.resource_uses:
                key = (
                    ("human_1", "human_1")
                    if use.resource_type == "HUMAN"
                    else (use.physical_resource_id or "unknown", use.component_id or "unknown")
                )
                result.append(Occupancy(key, assignment.interval, use, assignment.task_ids))
            continue
        if len(assignment.task_ids) != 1 or assignment.task_ids[0] not in scan.operations:
            continue
        task_id = assignment.task_ids[0]
        operation = scan.operations[task_id]
        unmatched = list(assignment.resource_uses)
        for required in operation.resource_requirements:
            options: tuple[str, ...]
            if required.resource_type == "HUMAN":
                options = ("human_1",)
            else:
                options = legal_device_options(operation, required, knowledge.profiles, scope)
            index = next(
                (
                    i
                    for i, use in enumerate(unmatched)
                    if use.resource_type == required.resource_type and use.resource_id in options
                ),
                None,
            )
            if index is None:
                scan.fail("RESOURCE_IDENTITY", "必需资源缺失或设备配置无效", task_id.root)
                continue
            use = unmatched.pop(index)
            if use.resource_type == "HUMAN":
                if use.resource_id != "human_1" or use.units != 1 or use.conflict_policy != "UNARY":
                    scan.fail("RESOURCE_IDENTITY", "人工只能是容量为1的human_1", task_id.root)
                key = ("human_1", "human_1")
                expected = required
            else:
                device = devices[use.resource_id]
                key = device.competition_key
                expected = required.model_copy(
                    update={
                        "resource_id": use.resource_id,
                        "physical_resource_id": device.physical_resource_id,
                        "component_id": device.component_id,
                    }
                )
            if use.model_dump(exclude={"layer_index"}) != expected.model_dump(
                exclude={"layer_index"}
            ):
                scan.fail("RESOURCE_IDENTITY", "实际资源参数或竞争策略偏离发布事实", task_id.root)
            # P2 当前载体只有完整工序区间；阶段占用必须明确实现后才接受。
            if use.occupancy is not None or use.occupation_policy != "WHOLE_INTERVAL":
                scan.fail("RESOURCE_PHASE", "P2 不接受未经展开的局部资源占用", task_id.root)
            result.append(Occupancy(key, assignment.interval, use, (task_id,)))
        if unmatched:
            scan.fail("RESOURCE_IDENTITY", "计划包含来源没有要求的资源", task_id.root)
    for execution in scan.runtime.executions:
        if execution.status not in {"RUNNING", "COMPLETED"}:
            continue
        explicit = (
            execution.scheduled_resource_spans
            if execution.status == "RUNNING"
            else execution.resource_spans
        )
        if explicit:
            if not set(execution.task_ids) <= set(scan.operations):
                continue
            for span in explicit:
                use = span.resource
                if (
                    not span.event_refs
                    or use.resource_id not in execution.resource_ids
                    and use.resource_type != "HUMAN"
                ):
                    scan.fail(
                        "HISTORY_RESOURCE", "阶段资源缺少实际绑定依据", execution.execution_id.root
                    )
                key = (
                    ("human_1", "human_1")
                    if use.resource_type == "HUMAN"
                    else (use.physical_resource_id or "unknown", use.component_id or "unknown")
                )
                result.append(Occupancy(key, span.interval, use, execution.task_ids))
            continue
        for task_id in execution.task_ids:
            historical_operation, interval = (
                scan.operations.get(task_id),
                scan.intervals.get(task_id),
            )
            if historical_operation is None or interval is None:
                continue
            for use in historical_operation.resource_requirements:
                if use.resource_type == "HUMAN":
                    result.append(Occupancy(("human_1", "human_1"), interval, use, (task_id,)))
                else:
                    options = legal_device_options(
                        historical_operation, use, knowledge.profiles, scope
                    )
                    chosen = [devices[r] for r in execution.resource_ids if r in options]
                    if len(chosen) != 1:
                        scan.fail(
                            "HISTORY_RESOURCE",
                            "执行事实缺少唯一合法设备选择",
                            execution.execution_id.root,
                        )
                        continue
                    device = chosen[0]
                    resolved = use.model_copy(
                        update={
                            "resource_id": device.device_instance_id,
                            "physical_resource_id": device.physical_resource_id,
                            "component_id": device.component_id,
                        }
                    )
                    result.append(Occupancy(device.competition_key, interval, resolved, (task_id,)))
    for occupancy in result:
        use = occupancy.use
        layer_device = devices.get(use.resource_id)
        if layer_device is None or layer_device.capacity == 1:
            if use.effective_layer_indices:
                scan.fail(
                    "RESOURCE_LAYER", "此资源没有已发布层位", *(t.root for t in occupancy.tasks)
                )
            continue
        if (
            not use.effective_layer_indices
            or any(layer > layer_device.capacity for layer in use.effective_layer_indices)
            or len(use.effective_layer_indices) != use.units
        ):
            scan.fail(
                "RESOURCE_LAYER", "设备层位缺失或超出已发布容量", *(t.root for t in occupancy.tasks)
            )
        if use.units > layer_device.capacity:
            scan.fail(
                "RESOURCE_CAPACITY",
                "任务占用超过设备已发布容量",
                *(t.root for t in occupancy.tasks),
            )
        if use.conflict_policy == "STATE_COMPATIBLE" and not {"temperature_c", "mode"} <= {
            c.parameter for c in use.configuration
        }:
            scan.fail(
                "RESOURCE_CONFIGURATION",
                "分层设备缺少明确温度或模式",
                *(t.root for t in occupancy.tasks),
            )
    return result


def check_resources(scan: Scan) -> None:
    occupancies = _resolve_occupancies(scan)
    devices = {d.device_instance_id: d for d in scan.knowledge.devices}
    contexts = {c.recipe_id: c for c in scan.knowledge.recipe_contexts}
    joint_coverage = [
        set(a.task_ids)
        for a in scan.candidate.assignments
        if a.carrier_id in {c.carrier_id for c in scan.problem.thermal_batch_candidates}
    ]
    joint_coverage.extend(
        set(e.task_ids)
        for e in scan.problem.fixed_executions
        if len({p.interval for p in e.task_spans}) > 1
    )
    hidden: set[int] = set()
    outer = []
    for instance in scan.problem.recipe_instances:
        context = contexts.get(instance.recipe_id)
        if context is None:
            continue
        tasks = {
            t.operation_id: t.task_id
            for t in scan.problem.logical_tasks
            if t.recipe_instance_id == instance.recipe_instance_id
        }
        for reservation in context.resource_reservations:
            members = tuple(tasks[o] for o in reservation.members if o in tasks)
            if len(members) != len(reservation.members) or any(
                t not in scan.intervals for t in members
            ):
                scan.fail("RESERVATION", "外层预约成员不完整", reservation.reservation_id)
                continue
            if any(set(members) <= covered for covered in joint_coverage):
                # Complete replacement is independently verified against source reservations.
                continue
            allowed = {
                devices[r].competition_key for r in reservation.resource_options if r in devices
            }
            selected = [
                (i, occ)
                for i, occ in enumerate(occupancies)
                if set(occ.tasks) & set(members) and occ.key in allowed
            ]
            keys = {occ.key for _, occ in selected}
            if len(keys) != 1:
                scan.fail(
                    "RESERVATION",
                    "同一外层预约的成员未选择同一物理设备",
                    reservation.reservation_id,
                )
                continue
            first = selected[0][1]
            device = devices.get(first.use.resource_id)
            if device is not None and device.capacity > 1:
                if len({occ.use.effective_layer_indices for _, occ in selected}) != 1:
                    scan.fail(
                        "RESERVATION_LAYER",
                        "同一完整预约必须保持同一层位",
                        reservation.reservation_id,
                    )
                if len({occ.use.units for _, occ in selected}) != 1:
                    scan.fail(
                        "RESERVATION_LAYER",
                        "同一完整预约的容量用量不一致",
                        reservation.reservation_id,
                    )
                configurations = {
                    tuple(
                        sorted(
                            (c.parameter, c.value)
                            for c in occ.use.configuration
                            if c.parameter != "duration_sec"
                        )
                    )
                    for _, occ in selected
                }
                if reservation.policy == "STATE_COMPATIBLE" and len(configurations) != 1:
                    scan.fail(
                        "RESOURCE_CONFIGURATION",
                        "完整分层预约的温度或模式发生变化",
                        reservation.reservation_id,
                    )
            hidden.update(i for i, _ in selected)
            outer.append(
                Occupancy(
                    first.key,
                    Interval(
                        start_sec=min(scan.intervals[t].start_sec for t in members),
                        end_sec=max(scan.intervals[t].end_sec for t in members),
                    ),
                    first.use.model_copy(update={"conflict_policy": reservation.policy}),
                    members,
                    instance.recipe_instance_id.root + ":" + reservation.reservation_id,
                )
            )
    effective = [o for i, o in enumerate(occupancies) if i not in hidden] + outer
    from app.validation.actual_occupancy import check_actual_occupancy

    check_actual_occupancy(scan, effective)
    from app.validation.transition_sequence import check_transition_sequence

    check_transition_sequence(scan, effective)
    grouped: dict[tuple[str, str], list[Occupancy]] = defaultdict(list)
    for occupancy in effective:
        grouped[occupancy.key].append(occupancy)
    for key, values in grouped.items():
        device = next((d for d in devices.values() if d.competition_key == key), None)
        layered = device is not None and device.capacity > 1
        points = sorted({t for o in values for t in (o.interval.start_sec, o.interval.end_sec)})
        for start, end in zip(points, points[1:], strict=False):
            active = [
                o for o in values if o.interval.start_sec <= start and o.interval.end_sec >= end
            ]
            refs = tuple(t.root for o in active for t in o.tasks)
            if layered and device is not None:
                if sum(o.use.units for o in active) > device.capacity:
                    scan.fail(
                        "RESOURCE_CAPACITY", f"物理资源{key}在[{start},{end})超过层位容量", *refs
                    )
                layers = [layer for o in active for layer in o.use.effective_layer_indices]
                if len(layers) != len(set(layers)):
                    scan.fail(
                        "RESOURCE_LAYER", f"物理资源{key}在[{start},{end})复用同一层位", *refs
                    )
            if len(active) < 2:
                continue
            if any(o.use.conflict_policy in {None, "UNARY", "BATCH_EXCLUSIVE"} for o in active):
                scan.fail("RESOURCE_OVERLAP", f"物理资源{key}在[{start},{end})重叠", *refs)
            else:
                if (
                    layered
                    and len(
                        {
                            tuple(
                                sorted(
                                    (c.parameter, c.value)
                                    for c in o.use.configuration
                                    if c.parameter != "duration_sec"
                                )
                            )
                            for o in active
                        }
                    )
                    > 1
                ):
                    scan.fail(
                        "RESOURCE_CONFIGURATION", "不同层同时在场必须使用相同温度及兼容模式", *refs
                    )
                configuration_values: dict[str, set[int | str]] = defaultdict(set)
                for occ in active:
                    for config in occ.use.configuration:
                        if config.parameter != "duration_sec":
                            configuration_values[config.parameter].add(config.value)
                if any(len(values) > 1 for values in configuration_values.values()):
                    scan.fail("RESOURCE_CONFIGURATION", "同时在场任务无共同设备配置", *refs)
    # 直接从运行状态核对不可用范围；不信任编译器是否生成了 ResourceBlock。
    for state in scan.runtime.device_states:
        key = (state.physical_resource_id, state.component_id)
        matching = [
            o
            for o in effective
            if o.key == key and o.interval.end_sec > scan.runtime.now_offset_sec
        ]
        if not matching:
            continue
        device = devices.get(state.device_instance_id)
        if device is None or device.competition_key != key:
            scan.fail("STATE_RESOURCE", "设备状态映射与发布事实不同", state.device_instance_id)
        if state.availability_status == "UNKNOWN" or (
            state.occupancy_status == "AWAITING_RELEASE_CONFIRMATION"
            and scan.runtime.details is None
        ):
            scan.fail("STATE_RESOURCE", "设备未知或缺少释放确认", state.device_instance_id)
        if state.availability_status == "UNAVAILABLE":
            recovery = (
                scan.runtime.time_origin.offset(state.expected_recovery_at)
                if state.expected_recovery_at
                else scan.problem.horizon_sec
            )
            for occ in matching:
                if occ.interval.start_sec < recovery:
                    scan.fail("STATE_RESOURCE", "设备在恢复前被使用", state.device_instance_id)
        if state.occupancy_status == "OCCUPIED" and scan.runtime.details is None:
            execution = next(
                (e for e in scan.runtime.executions if e.execution_id == state.active_execution_id),
                None,
            )
            if execution is None or execution.status != "RUNNING":
                scan.fail("STATE_RESOURCE", "设备占用没有对应运行事实", state.device_instance_id)
