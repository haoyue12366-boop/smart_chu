"""扫描内按完整字段比较物料，避免复制契约对象或重复深层模型比较。"""

from app.domain.material import MaterialRequirement, QuantityKind
from app.domain.quantity import ScaledQuantity

type QuantityValue = tuple[type[object], int, str, type[object], int]
type RequirementValue = tuple[
    str, str, QuantityKind, QuantityValue | None, QuantityValue | None, str | None, tuple[str, ...]
]


def _quantity_value(quantity: ScaledQuantity | None) -> QuantityValue | None:
    if quantity is None:
        return None
    # bool/float 与 int 可能数值相等；独立核验仍须保护严格整数契约。
    return type(quantity.value), quantity.value, quantity.unit, type(quantity.scale), quantity.scale


def requirement_value(
    requirement: MaterialRequirement,
    *,
    spec_id: str | None = None,
    requirement_id: str | None = None,
) -> RequirementValue:
    return (
        requirement.requirement_id if requirement_id is None else requirement_id,
        requirement.spec_id if spec_id is None else spec_id,
        requirement.quantity_kind,
        _quantity_value(requirement.quantity),
        _quantity_value(requirement.upper_quantity),
        requirement.qualitative_quantity,
        requirement.provenance_refs,
    )
