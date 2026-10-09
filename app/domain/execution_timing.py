"""执行时间的纯投影；预计结束不表示占用已实际释放。"""

from app.domain.ids import TaskId
from app.domain.resources import DeviceInstance, ResourceUse
from app.domain.runtime_snapshot import ExecutionRecord, RuntimeSnapshot
from app.domain.time import Interval


def execution_intervals(
    record: ExecutionRecord, runtime: RuntimeSnapshot
) -> dict[TaskId, Interval]:
    if record.task_spans:
        return {p.task_id: p.interval for p in record.task_spans}
    if record.started_at is None:
        raise ValueError("冻结事实缺少开始时刻")
    start = runtime.time_origin.offset(record.started_at)
    if record.finished_at is not None:
        end = runtime.time_origin.offset(record.finished_at)
    elif record.remaining_sec is not None and record.remaining_source_ref:
        end = runtime.now_offset_sec + record.remaining_sec
    else:
        raise ValueError("运行事实缺少剩余时间")
    return {t: Interval(start_sec=start, end_sec=end) for t in record.task_ids}


def execution_resource_uses(
    record: ExecutionRecord,
    interval: Interval,
    requirements: tuple[ResourceUse, ...],
    devices: tuple[DeviceInstance, ...],
) -> tuple[ResourceUse, ...]:
    """读取冻结资源事实，保留真实灶眼选择；不创建占用或重排约束。"""
    explicit = (
        record.scheduled_resource_spans if record.status == "RUNNING" else record.resource_spans
    )
    resources: list[ResourceUse] = []
    for required in requirements:
        if explicit:
            selected = tuple(
                span.resource
                for span in explicit
                if span.interval.start_sec < interval.end_sec
                and interval.start_sec < span.interval.end_sec
                and span.resource.resource_type == required.resource_type
                and (
                    required.resource_type == "HUMAN"
                    or span.resource.physical_resource_id == required.physical_resource_id
                    or (
                        required.resource_id == "stove_choice"
                        and span.resource.physical_resource_id in {"stove_1", "stove_2"}
                    )
                )
            )
            if not selected:
                raise ValueError("冻结工序缺少相应区间的实际资源事实")
            resources.extend(selected)
        elif required.resource_type == "HUMAN":
            resources.append(required)
        else:
            matches = [
                device
                for device in devices
                if device.device_instance_id in record.resource_ids
                and (
                    device.physical_resource_id == required.physical_resource_id
                    or (
                        required.resource_id == "stove_choice"
                        and device.physical_resource_id in {"stove_1", "stove_2"}
                    )
                )
            ]
            if len(matches) != 1:
                raise ValueError("冻结工序缺少唯一的实际设备选择")
            device = matches[0]
            resources.append(
                required.model_copy(
                    update={
                        "resource_id": device.device_instance_id,
                        "physical_resource_id": device.physical_resource_id,
                        "component_id": device.component_id,
                    }
                )
            )
    return tuple(dict.fromkeys(resources))
