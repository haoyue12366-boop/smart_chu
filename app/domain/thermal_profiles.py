"""从完整资源配置定位唯一已发布热配置，缺失或歧义不猜测。"""

from app.domain.resources import DeviceInstance, DeviceProfile, ResourceUse
from app.domain.transitions import ThermalProfile


def thermal_profile(
    use: ResourceUse, devices: tuple[DeviceInstance, ...], profiles: tuple[DeviceProfile, ...]
) -> ThermalProfile | None:
    device = next((d for d in devices if d.device_instance_id == use.resource_id), None)
    configuration = {
        c.parameter: c.value for c in use.configuration if c.parameter != "duration_sec"
    }
    if (
        device is None
        or not device.physical_resource_id
        or not device.component_id
        or "mode" not in configuration
    ):
        return None
    matching = [
        p
        for p in profiles
        if p.profile_id in device.capability_refs
        and (not use.profile_options or p.profile_id in use.profile_options)
        and p.mode == configuration["mode"]
    ]
    if len(matching) != 1:
        return None
    return ThermalProfile(
        physical_resource_id=device.physical_resource_id,
        component_id=device.component_id,
        profile_id=matching[0].profile_id,
        configuration=tuple(c for c in use.configuration if c.parameter != "duration_sec"),
    )
