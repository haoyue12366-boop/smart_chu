"""运行数量的精确有理数，不隐式取整。"""

from fractions import Fraction

from app.domain.base import FrozenModel, NonNegativeInt, PositiveInt


class RationalAmount(FrozenModel):
    numerator: NonNegativeInt
    denominator: PositiveInt = 1

    def fraction(self) -> Fraction:
        return Fraction(self.numerator, self.denominator)

    @classmethod
    def of(cls, value: Fraction) -> "RationalAmount":
        return cls(numerator=value.numerator, denominator=value.denominator)
