from __future__ import annotations

from enum import StrEnum

from pydantic import model_validator

from app.domain.base import FrozenModel, NonEmpty, PositiveInt, ReviewStatus
from app.domain.quantity import ScaledQuantity


class MaterialSpec(FrozenModel):
    spec_id: NonEmpty
    ingredient_id: NonEmpty
    name: NonEmpty
    state: NonEmpty
    shape: str | None = None
    size_mm: PositiveInt | None = None
    treatment: str | None = None
    composition: tuple[NonEmpty, ...] = ()
    provenance_refs: tuple[NonEmpty, ...] = ()
    review_status: ReviewStatus = ReviewStatus.DRAFT


class QuantityKind(StrEnum):
    EXACT = "EXACT"
    RANGE = "RANGE"
    QUALITATIVE = "QUALITATIVE"
    UNKNOWN = "UNKNOWN"


class MaterialRequirement(FrozenModel):
    requirement_id: NonEmpty
    spec_id: NonEmpty
    quantity_kind: QuantityKind
    quantity: ScaledQuantity | None = None
    upper_quantity: ScaledQuantity | None = None
    qualitative_quantity: str | None = None
    provenance_refs: tuple[NonEmpty, ...] = ()

    @property
    def exact_quantity(self) -> ScaledQuantity | None:
        """范围下界仅是配方边界，不能作为库存实数或实际报告的默认量。"""
        return self.quantity if self.quantity_kind == QuantityKind.EXACT else None

    @model_validator(mode="after")
    def quantity_contract(self) -> MaterialRequirement:
        if self.quantity_kind in (QuantityKind.EXACT, QuantityKind.RANGE) and self.quantity is None:
            raise ValueError("精确量或范围量必须有明确数值和单位")
        if self.quantity_kind == QuantityKind.RANGE:
            if self.upper_quantity is None or self.quantity is None:
                raise ValueError("范围必须有上界")
            upper = self.upper_quantity.convert(self.quantity.unit, self.quantity.scale)
            if upper.value < self.quantity.value:
                raise ValueError("数量范围倒置")
        if self.quantity_kind == QuantityKind.QUALITATIVE and not self.qualitative_quantity:
            raise ValueError("定性量保留原文")
        return self


class MaterialLoss(FrozenModel):
    spec_id: NonEmpty
    quantity: ScaledQuantity
    reason: NonEmpty
    provenance_refs: tuple[NonEmpty, ...]
