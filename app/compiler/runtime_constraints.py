"""设备可用性与占用事实分开处理；未知释放时间不能自动清空。"""

from app.domain.knowledge import MenuKnowledgeView
from app.domain.runtime_constraints import ResourceBlock
from app.domain.runtime_snapshot import RuntimeSnapshot
from app.domain.thermal import ExpandedPrograms
from app.domain.time import Interval


def compile_resource_blocks(
    knowledge: MenuKnowledgeView,
    runtime: RuntimeSnapshot,
    used_device_ids: set[str],
    horizon_sec: int,
    programs: ExpandedPrograms | None = None,
) -> tuple[ResourceBlock, ...]:
    devices = {d.device_instance_id: d for d in knowledge.devices}
    used_physical_keys = {devices[key].competition_key for key in used_device_ids}
    executions = {e.execution_id: e for e in runtime.executions}
    result = []
    if runtime.details:
        for occupied in runtime.details.occupancies:
            if occupied.released_at is not None:
                continue
            record = executions.get(occupied.execution_id)
            if (
                record is not None
                and record.status in {"COMPLETED", "RUNNING"}
                and not occupied.awaiting_confirmation
                and any(
                    reservation.reservation_id == occupied.reservation_id
                    and set(reservation.members) == set(occupied.reservation_members)
                    and set(record.task_ids) <= set(reservation.members)
                    and not set(reservation.members) & set(runtime.details.retired_task_ids)
                    and (occupied.resource.physical_resource_id, occupied.resource.component_id)
                    in {
                        devices[r].competition_key
                        for r in reservation.resource_options
                        if r in devices
                    }
                    for reservation in (programs.reservations if programs else ())
                )
            ):
                continue  # 该占用由保留真实起点和设备的完整外层预约延续。
            if (
                record is not None
                and record.status == "RUNNING"
                and not occupied.awaiting_confirmation
                and not set(record.task_ids) <= set(runtime.details.retired_task_ids)
            ):
                continue
            use = occupied.resource
            if (
                use.resource_type != "HUMAN"
                and (use.physical_resource_id, use.component_id) not in used_physical_keys
            ):
                continue
            result.append(
                ResourceBlock(
                    occupancy_use=use,
                    resource_id=use.resource_id,
                    physical_resource_id=use.physical_resource_id or use.resource_id,
                    component_id=use.component_id or use.resource_id,
                    interval=Interval(start_sec=runtime.now_offset_sec, end_sec=horizon_sec),
                    reason="实际占用尚未释放",
                    evidence_refs=(occupied.occupancy_id,),
                )
            )
    for state in runtime.device_states:
        device = devices.get(state.device_instance_id)
        if device is None or device.competition_key != (
            state.physical_resource_id,
            state.component_id,
        ):
            raise ValueError("运行设备物理映射与知识不一致")
        if device.competition_key not in used_physical_keys:
            continue
        if state.availability_status == "UNKNOWN" and runtime.details is None:
            raise ValueError("实际必需设备可用状态未知")
        if state.occupancy_status == "AWAITING_RELEASE_CONFIRMATION" and runtime.details is None:
            raise ValueError("设备尚未确认释放，不能猜测空闲时刻")
        if state.occupancy_status == "OCCUPIED" and runtime.details is None:
            if state.active_execution_id is None:
                raise ValueError("设备占用缺少执行身份")
            execution = executions.get(state.active_execution_id)
            if (
                execution is None
                or execution.status != "RUNNING"
                or execution.remaining_sec is None
            ):
                raise ValueError("设备占用缺少运行工序或明确剩余时长")
            if state.device_instance_id not in execution.resource_ids:
                raise ValueError("实际占用与执行资源不一致")
        if state.availability_status in {"UNAVAILABLE", "UNKNOWN"}:
            end = (
                runtime.time_origin.offset(state.expected_recovery_at)
                if state.expected_recovery_at
                else horizon_sec
            )
            if end <= runtime.now_offset_sec:
                if runtime.details is None:
                    raise ValueError("设备恢复预测已过期，不能推定已经恢复")
                end = horizon_sec
            result.append(
                ResourceBlock(
                    resource_id=state.device_instance_id,
                    physical_resource_id=state.physical_resource_id,
                    component_id=state.component_id,
                    interval=Interval(start_sec=runtime.now_offset_sec, end_sec=end),
                    reason="实际设备不可用",
                    evidence_refs=tuple(e.root for e in runtime.event_refs),
                )
            )
    return tuple(result)
