"""只读会话展示；设备可用性观测和实际执行占用分别投影。"""

from datetime import timedelta
from typing import Literal

from app.domain.presentation import DevicePresentation
from app.domain.runtime_clock import clock_offset
from app.services.container import ServiceContainer


def read_session(
    services: ServiceContainer,
    session_id: str,
    *,
    view: Literal["full", "workbench"] = "full",
) -> dict[str, object]:
    runtime, _ = services.for_session(session_id)
    session = runtime.get(session_id)
    state = session.runtime
    details = state.details
    occupancies = details.occupancies if details else ()
    seen: set[tuple[str, str]] = set()
    devices: list[dict[str, object]] = []
    for device in runtime.knowledge.devices:
        key = device.competition_key
        if key in seen:
            continue
        seen.add(key)
        observations = [
            d for d in state.device_states if (d.physical_resource_id, d.component_id) == key
        ]
        observed = max(observations, key=lambda d: d.observed_at, default=None)
        history = [
            o
            for o in occupancies
            if (o.resource.physical_resource_id, o.resource.component_id) == key
        ]
        active = [o for o in history if o.released_at is None]
        executions = tuple(dict.fromkeys(o.execution_id.root for o in active))
        devices.append(
            DevicePresentation.model_validate(
                {
                    "device_instance_id": device.device_instance_id,
                    "physical_resource_id": key[0],
                    "component_id": key[1],
                    "availability_status": observed.availability_status.value
                    if observed
                    else "UNOBSERVED",
                    "occupancy_status": "AWAITING_RELEASE_CONFIRMATION"
                    if any(o.awaiting_confirmation for o in active)
                    else "OCCUPIED"
                    if active
                    else "FREE"
                    if observed or history
                    else "UNOBSERVED",
                    "active_execution_id": executions[0] if executions else None,
                    "active_execution_ids": executions,
                    "configuration": [
                        item.model_dump(mode="json")
                        for item in (
                            active[0].resource.configuration
                            if active
                            else observed.configuration
                            if observed
                            else ()
                        )
                    ],
                }
            ).model_dump(mode="json")
        )
    clock = session.schedule_clock
    current = clock_offset(session, runtime.clock.now())
    boundary = clock.replan_not_before_sec if clock else None
    boundary_at = (
        clock.started_at + timedelta(seconds=boundary - clock.start_offset_sec)
        if clock is not None and boundary is not None
        else None
    )
    progress = {
        "enabled": state.execution_mode == "SCHEDULE_CLOCK",
        "started_at": clock.started_at.isoformat() if clock else None,
        "current_offset_sec": current,
        "replan_requested_sec": clock.replan_requested_sec if clock else None,
        "replan_not_before_sec": boundary,
        "replan_not_before_at": boundary_at.isoformat() if boundary_at else None,
        "waiting_for_boundary": boundary is not None and current < boundary,
    }
    visible = (
        session.model_dump(
            mode="json",
            include={
                "runtime": {
                    "session_id": True,
                    "state_revision": True,
                    "current_plan_version": True,
                    "knowledge_version": True,
                    "time_origin": True,
                    "now_offset_sec": True,
                    "execution_mode": True,
                    "executions": True,
                    "device_states": True,
                    "material_lots": True,
                    "details": {"cancelled_instance_ids", "lots"},
                },
                "menu": True,
                "status": True,
                "requires_replan": True,
                "dispatch_blocked": True,
                "last_planning_failure": True,
                "replan_reasons": True,
            },
        )
        if view == "workbench"
        else session.model_dump(mode="json")
    )
    return {
        **visible,
        "device_presentation": devices,
        "clock_progress": progress,
    }
