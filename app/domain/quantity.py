"""精确有理数量；质量与体积不隐式转换。"""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction
from typing import Literal

from app.domain.base import FrozenModel, NonNegativeInt, PositiveInt

Unit = Literal["g", "kg", "ml", "L", "个", "片", "根", "张", "瓣", "朵", "汤匙", "茶匙"]
_UNITS: dict[str, tuple[str, int]] = {
    "g": ("mass", 1),
    "kg": ("mass", 1000),
    "ml": ("volume", 1),
    "L": ("volume", 1000),
    **{u: (u, 1) for u in ("个", "片", "根", "张", "瓣", "朵", "汤匙", "茶匙")},
}


class ScaledQuantity(FrozenModel):
    value: NonNegativeInt
    unit: Unit
    scale: PositiveInt

    @classmethod
    def from_decimal(cls, value: Decimal, unit: Unit, scale: int) -> ScaledQuantity:
        if not value.is_finite():
            raise ValueError("数量必须有限")
        scaled = Fraction(value) * scale
        if scaled.denominator != 1:
            raise ValueError("缩放会丢失精度")
        return cls(value=scaled.numerator, unit=unit, scale=scale)

    def as_decimal(self) -> Decimal:
        return Decimal(self.value) / Decimal(self.scale)

    def convert(self, unit: Unit, scale: int) -> ScaledQuantity:
        source_dimension, source_factor = _UNITS[self.unit]
        target_dimension, target_factor = _UNITS[unit]
        if source_dimension != target_dimension:
            raise ValueError("不同量纲缺少审核换算")
        exact = Fraction(self.value * source_factor * scale, self.scale * target_factor)
        if exact.denominator != 1:
            raise ValueError("换算会丢失精度")
        return ScaledQuantity(value=exact.numerator, unit=unit, scale=scale)

    def add(self, other: ScaledQuantity) -> ScaledQuantity:
        # 取公共整数缩放，不能为沿用左值精度而截断右值。
        left_dimension, left_factor = _UNITS[self.unit]
        right_dimension, right_factor = _UNITS[other.unit]
        if left_dimension != right_dimension:
            raise ValueError("不同量纲缺少审核换算")
        total = Fraction(self.value, self.scale) + Fraction(
            other.value * right_factor, other.scale * left_factor
        )
        return ScaledQuantity(value=total.numerator, unit=self.unit, scale=total.denominator)
