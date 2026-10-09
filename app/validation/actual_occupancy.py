"""独立检查未释放的真实占用，不依赖 Compiler 的阻塞清单。"""

from typing import TYPE_CHECKING

from app.domain.candidates import stable_id
from app.domain.runtime_facts import Occupancy as ActualOccupancy
from app.validation.schedule_context import Scan

if TYPE_CHECKING:
    from app.validation.resources import Occupancy


def check_actual_occupancy(scan: Scan, planned: list["Occupancy"]) -> None:
    details = scan.runtime.details
    if details is None:
        return
    records = {e.execution_id: e for e in scan.runtime.executions}
    fixed = {e.execution_id for e in scan.problem.fixed_executions}
    devices = {device.device_instance_id: device for device in scan.knowledge.devices}
    blocking: list[ActualOccupancy] = []
    for occupied in details.occupancies:
        if occupied.released_at is not None:
            continue
        record = records.get(occupied.execution_id)
        if record is None:
            scan.fail("STATE_RESOURCE", "实际占用缺少执行身份", occupied.occupancy_id)
            continue
        # 在当前剩余问题内的运行事实已按冻结阶段独立扫描；其余占用不能靠取消菜单消失。
        if (
            record.execution_id in fixed
            and record.status == "RUNNING"
            and not occupied.awaiting_confirmation
        ):
            continue
        use = occupied.resource
        key = (use.physical_resource_id or use.resource_id, use.component_id or use.resource_id)
        device = devices.get(use.resource_id)
        layered = device is not None and device.capacity > 1
        if (
            layered
            and device is not None
            and (
                not use.effective_layer_indices
                or any(layer > device.capacity for layer in use.effective_layer_indices)
                or len(use.effective_layer_indices) != use.units
            )
        ):
            scan.fail("STATE_RESOURCE", "实际占用缺少有效设备层位", occupied.occupancy_id)
        if (
            occupied.reservation_id
            and not occupied.awaiting_confirmation
            and record.status == "COMPLETED"
        ):
            # 从原知识重建范围，不能相信 Compiler 的预约或占用上的单个自报 ID。
            contexts = {context.recipe_id: context for context in scan.knowledge.recipe_contexts}
            continued = False
            for instance in scan.problem.recipe_instances:
                context = contexts.get(instance.recipe_id)
                if context is None:
                    continue
                for reservation in context.resource_reservations:
                    identity = stable_id(
                        "reservation", instance.recipe_instance_id.root, reservation.reservation_id
                    )
                    members = {
                        stable_id("task", instance.recipe_instance_id.root, operation.root)
                        for operation in reservation.members
                    }
                    if (
                        identity != occupied.reservation_id
                        or members != {task.root for task in occupied.reservation_members}
                        or not {task.root for task in record.task_ids} <= members
                        or key
                        not in {
                            devices[r].competition_key
                            for r in reservation.resource_options
                            if r in devices
                        }
                    ):
                        continue
                    continued = any(
                        item.key == key
                        and item.use.effective_layer_indices == use.effective_layer_indices
                        and item.use.units == use.units
                        and {task.root for task in item.tasks} == members
                        and item.interval.start_sec
                        <= scan.runtime.time_origin.offset(occupied.started_at)
                        and item.interval.end_sec > scan.runtime.now_offset_sec
                        for item in planned
                    )
            if continued:
                continue  # 此外层预约仍参与与其他菜的独立物理冲突扫描。
        blocking.append(occupied)
        for item in planned:
            if item.key != key or item.interval.end_sec <= scan.runtime.now_offset_sec:
                continue
            unary = {None, "UNARY", "BATCH_EXCLUSIVE"}
            incompatible = use.conflict_policy in unary or item.use.conflict_policy in unary
            incompatible |= bool(
                set(use.effective_layer_indices) & set(item.use.effective_layer_indices)
            )
            actual_config = {
                c.parameter: c.value for c in use.configuration if c.parameter != "duration_sec"
            }
            future_config = {
                c.parameter: c.value
                for c in item.use.configuration
                if c.parameter != "duration_sec"
            }
            incompatible |= any(
                actual_config[p] != future_config[p]
                for p in actual_config.keys() & future_config.keys()
            )
            if layered:
                incompatible |= (
                    not {"temperature_c", "mode"} <= actual_config.keys()
                    or not {"temperature_c", "mode"} <= future_config.keys()
                    or actual_config != future_config
                )
            if incompatible:
                scan.fail(
                    "STATE_RESOURCE",
                    "计划使用了尚未确认释放的真实资源",
                    occupied.occupancy_id,
                    *(t.root for t in item.tasks),
                )
    for device in devices.values():
        if device.capacity <= 1:
            continue
        held = [
            item
            for item in blocking
            if (item.resource.physical_resource_id, item.resource.component_id)
            == device.competition_key
        ]
        if not held:
            continue
        future = [
            item
            for item in planned
            if item.key == device.competition_key
            and item.interval.end_sec > scan.runtime.now_offset_sec
        ]
        points = sorted(
            {scan.runtime.now_offset_sec}
            | {max(scan.runtime.now_offset_sec, item.interval.start_sec) for item in future}
            | {item.interval.end_sec for item in future}
        )
        for start, end in zip(points, points[1:], strict=False):
            active = [
                item
                for item in future
                if item.interval.start_sec <= start and item.interval.end_sec >= end
            ]
            if (
                sum(item.resource.units for item in held) + sum(item.use.units for item in active)
                > device.capacity
            ):
                scan.fail(
                    "STATE_RESOURCE",
                    "计划与尚未释放的实际占用超过设备层位容量",
                    *(item.occupancy_id for item in held),
                    *(task.root for item in active for task in item.tasks),
                )
