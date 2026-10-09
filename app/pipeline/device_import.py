"""设备清单只读导入；保留范围原文，不推定程序时长或物理实例。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError

from app.domain.base import FrozenModel, NonEmpty
from app.domain.source_document import (
    DeviceSourceDocument,
    RawDevice,
    RawDeviceParameter,
    RawDeviceVariant,
    SourceField,
)
from app.pipeline.import_raw import SourceImportError, read_source


class _ParameterInput(FrozenModel):
    name: NonEmpty = Field(alias="参数名")
    kind: Literal["整数", "枚举", "枚举对象"] = Field(alias="类型")
    range_text: str | None = Field(default=None, alias="范围")
    description: str | None = Field(default=None, alias="说明")
    choices: tuple[str | dict[str, str], ...] = Field(default=(), alias="可选值")


class _DeviceInput(FrozenModel):
    name: NonEmpty = Field(alias="设备名称")
    parameters: tuple[_ParameterInput, ...] = Field(alias="参数", min_length=1)


class _DocumentInput(FrozenModel):
    devices: tuple[_DeviceInput, ...] = Field(alias="设备清单", min_length=1)


def _parameter(value: _ParameterInput) -> RawDeviceParameter:
    choices: list[str] = []
    variants: list[RawDeviceVariant] = []
    if value.kind == "整数":
        if not value.range_text or value.choices:
            raise ValueError("整数参数必须有原文范围且不混用枚举")
    elif not value.choices:
        raise ValueError("枚举参数缺少可选值")
    for item in value.choices:
        if value.kind == "枚举" and isinstance(item, str) and item.strip():
            choices.append(item)
        elif value.kind == "枚举对象" and isinstance(item, dict) and item.get("模式名"):
            variants.append(
                RawDeviceVariant(
                    name=item["模式名"],
                    fields=tuple(SourceField(name=k, value=v) for k, v in item.items()),
                )
            )
        else:
            raise ValueError(f"参数 {value.name} 的可选值与类型不一致")
    names = [*choices, *(variant.name for variant in variants)]
    if len(names) != len(set(names)):
        raise ValueError(f"参数 {value.name} 存在重复选项")
    return RawDeviceParameter(
        name=value.name,
        kind=value.kind,
        range_text=value.range_text,
        description=value.description,
        choices=tuple(choices),
        variants=tuple(variants),
    )


def import_devices(path: Path) -> DeviceSourceDocument:
    """保留文件哈希与 JSON 定位，缺失参数不会在导入时补成默认值。"""
    text, encoding, digest = read_source(path)
    try:
        source = _DocumentInput.model_validate(json.loads(text))
        if len({device.name for device in source.devices}) != len(source.devices):
            raise ValueError("设备名称重复")
        devices: list[RawDevice] = []
        for index, device in enumerate(source.devices):
            if len({p.name for p in device.parameters}) != len(device.parameters):
                raise ValueError(f"设备 {device.name} 的参数名重复")
            devices.append(
                RawDevice(
                    name=device.name,
                    locator=f"/设备清单/{index}",
                    parameters=tuple(_parameter(p) for p in device.parameters),
                )
            )
    except (ValueError, ValidationError) as exc:
        raise SourceImportError(f"{path.name} 设备清单损坏：{exc}") from exc
    return DeviceSourceDocument(
        source_file=path.name,
        source_sha256=digest,
        encoding=encoding,
        raw_json=text,
        devices=tuple(devices),
    )
