"""依据设备清单构造待审核能力，不补写未知程序时长或物理部件。"""

import re

from app.domain.resources import DeviceProfile, ParameterConstraint
from app.domain.source_document import DeviceSourceDocument, RawDeviceParameter


def _range(name: str, text: str, *, suffix: str = "", factor: int = 1) -> ParameterConstraint:
    match = re.fullmatch(r"(-?\d+)~(-?\d+)" + re.escape(suffix), text)
    if match is None:
        raise ValueError(f"设备参数范围格式不支持：{name}")
    return ParameterConstraint(
        parameter=name, minimum=int(match[1]) * factor, maximum=int(match[2]) * factor
    )


def build_device_profiles(
    source: DeviceSourceDocument,
    rule_version: str,
) -> tuple[DeviceProfile, ...]:
    profiles = []
    evidence = (f"device-source:{source.source_sha256}",)

    def add(device: str, mode: str, constraints: tuple[ParameterConstraint, ...]) -> None:
        profiles.append(
            DeviceProfile(
                profile_id=f"{device}:{mode}",
                device_type=device,
                mode=mode,
                constraints=constraints,
                rule_version=rule_version,
                provenance_refs=evidence,
                review_status="NEEDS_REVIEW",
            )
        )

    for device in source.devices:
        params = {p.name: p for p in device.parameters}
        if device.name in {"蒸箱", "烤箱"}:
            parameter = next(p for p in device.parameters if p.kind == "枚举对象")
            for variant in parameter.variants:
                fields = {f.name: f.value for f in variant.fields}
                constraints = [
                    _range("temperature_c", fields["烹饪温度"], suffix="℃"),
                    _range("duration_sec", fields["烹饪时长"], suffix="分钟", factor=60),
                ]
                if "湿度" in fields:
                    constraints.append(
                        ParameterConstraint(
                            parameter="humidity", allowed_values=tuple(fields["湿度"].split("/"))
                        )
                    )
                add(device.name, variant.name, tuple(constraints))
        elif device.name == "灶具":
            add(device.name, "灶具", (_range("power_level", params["火力档位"].range_text or ""),))
        elif device.name == "冰箱":
            for mode in params["工作模式"].choices:
                if mode in {"冷藏", "冷冻"}:
                    temperature = params[f"{mode}设定温度"]
                    add(
                        device.name,
                        mode,
                        (_range("temperature_c", temperature.range_text or "", suffix="℃"),),
                    )
                else:
                    add(device.name, mode, ())
        elif device.name == "洗碗机":
            for mode in params["洗涤程序"].choices:
                add(device.name, mode, ())
        elif device.name == "烟机":
            add(
                device.name,
                "排烟",
                (
                    ParameterConstraint(
                        parameter="fan_level", allowed_values=params["档位"].choices
                    ),
                ),
            )
        elif device.name == "净咖一体机":
            water: RawDeviceParameter = params["即热出水温度"]
            if not all(re.fullmatch(r"\d+℃", v) for v in water.choices):
                raise ValueError("出水温度档位格式不支持")
            add(
                device.name,
                "即热出水",
                (
                    ParameterConstraint(
                        parameter="temperature_c",
                        allowed_values=tuple(int(v[:-1]) for v in water.choices),
                    ),
                ),
            )
            for mode in params["咖啡模式"].choices:
                add(device.name, mode, ())
        else:
            raise ValueError(f"设备类型缺少明确参数映射：{device.name}")
    return tuple(profiles)
