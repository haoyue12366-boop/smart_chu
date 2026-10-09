"""只读来源契约；编号原文与规范化原子操作保持独立。"""

from typing import Literal, Self

from pydantic import model_validator

from app.domain.base import Digest, FrozenModel, NonEmpty, NonNegativeInt, PositiveInt
from app.domain.ids import RecipeId


class SourceField(FrozenModel):
    name: NonEmpty
    value: str


class SourceStep(FrozenModel):
    number: PositiveInt
    start_offset: NonNegativeInt
    end_offset: PositiveInt
    text: NonEmpty


class SourceDocument(FrozenModel):
    recipe_id: RecipeId
    name: NonEmpty
    ingredients_text: str
    steps_text: NonEmpty
    source_file: NonEmpty
    source_sha256: Digest
    encoding: NonEmpty
    row_number: PositiveInt
    line_start: PositiveInt
    line_end: PositiveInt
    raw_record: tuple[SourceField, ...]
    steps: tuple[SourceStep, ...]

    @model_validator(mode="after")
    def check_locators(self) -> Self:
        if self.line_end < self.line_start:
            raise ValueError("原文行号范围倒置")
        for step in self.steps:
            if self.steps_text[step.start_offset : step.end_offset] != step.text:
                raise ValueError("步骤定位与原文不一致")
        return self


class RawDeviceVariant(FrozenModel):
    name: NonEmpty
    fields: tuple[SourceField, ...]


class RawDeviceParameter(FrozenModel):
    name: NonEmpty
    kind: Literal["整数", "枚举", "枚举对象"]
    range_text: str | None = None
    description: str | None = None
    choices: tuple[str, ...] = ()
    variants: tuple[RawDeviceVariant, ...] = ()


class RawDevice(FrozenModel):
    name: NonEmpty
    locator: NonEmpty
    parameters: tuple[RawDeviceParameter, ...]


class DeviceSourceDocument(FrozenModel):
    source_file: NonEmpty
    source_sha256: Digest
    encoding: NonEmpty
    raw_json: NonEmpty
    devices: tuple[RawDevice, ...]
