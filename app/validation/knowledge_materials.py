"""独立检查物料来源、生产依赖和有依据的份额守恒。"""

import re
from collections import defaultdict
from fractions import Fraction

from app.domain.canonical_recipe import CanonicalRecipeModel
from app.domain.ids import OperationId
from app.domain.material import MaterialRequirement
from app.domain.quantity import ScaledQuantity
from app.domain.validation_contract import ValidationViolation


def _share(demand: MaterialRequirement, supply: MaterialRequirement) -> Fraction:
    if demand.quantity is not None and supply.quantity is not None:
        zero = ScaledQuantity(value=0, unit=supply.quantity.unit, scale=1)
        quantity = zero.add(demand.quantity)
        total = Fraction(supply.quantity.value, supply.quantity.scale)
        if total <= 0:
            raise ValueError("供给量必须大于零")
        share = Fraction(quantity.value, quantity.scale) / total
        if demand.upper_quantity is not None:
            if supply.upper_quantity is None:
                raise ValueError("范围消耗没有供给上界")
            high = zero.add(demand.upper_quantity)
            if Fraction(high.value, high.scale) != share * Fraction(
                supply.upper_quantity.value, supply.upper_quantity.scale
            ):
                raise ValueError("数量上下界使用的份额不一致")
        return share
    match = re.search(
        r"(?:原配方份额|本物料整批的)(\d+(?:/\d+)?)", demand.qualitative_quantity or ""
    )
    if match is None or demand.quantity is not None or supply.quantity is not None:
        raise ValueError("物料数量或定性整批份额未明确")
    return Fraction(match[1])


def validate_material_paths(
    recipe: CanonicalRecipeModel, ancestors: dict[OperationId, set[OperationId]]
) -> tuple[ValidationViolation, ...]:
    violations = []

    def fail(code: str, spec: str, message: str) -> None:
        violations.append(
            ValidationViolation(
                code=code,
                message=message,
                entity_refs=(recipe.recipe_id.root, spec),
                evidence_refs=recipe.provenance_refs,
            )
        )

    supplies = {m.spec_id: m for m in recipe.ingredient_requirements}
    if len(supplies) != len(recipe.ingredient_requirements):
        fail("MATERIAL_DUPLICATE", recipe.recipe_id.root, "同一物料有重复原料供给")
    producers = {}
    for op in recipe.operations:
        for output in op.material_outputs:
            if output.spec_id in supplies:
                fail("MATERIAL_DUPLICATE", output.spec_id, "物料生产身份重复")
            supplies[output.spec_id] = output
            producers[output.spec_id] = op.operation_id
    consumed: dict[str, Fraction] = defaultdict(Fraction)
    for op in recipe.operations:
        demands = list(op.material_inputs)
        for loss in op.losses:
            demands.append(
                MaterialRequirement(
                    requirement_id=f"loss:{op.operation_id.root}",
                    spec_id=loss.spec_id,
                    quantity_kind="EXACT",
                    quantity=loss.quantity,
                    provenance_refs=loss.provenance_refs,
                )
            )
        for demand in demands:
            supply = supplies.get(demand.spec_id)
            if supply is None:
                fail("MATERIAL_SOURCE_MISSING", demand.spec_id, "消耗的物料没有输入或生产者")
                continue
            producer = producers.get(demand.spec_id)
            if producer is not None and producer not in ancestors.get(op.operation_id, set()):
                fail("MATERIAL_DEPENDENCY_MISSING", demand.spec_id, "生产者没有先于消耗者的路径")
            try:
                share = _share(demand, supply)
                if share <= 0:
                    raise ValueError("消耗份额必须为正")
                consumed[demand.spec_id] += share
                if consumed[demand.spec_id] > 1:
                    fail("MATERIAL_OVERCONSUMED", demand.spec_id, "累计消耗超过物料供给")
            except (ValueError, ZeroDivisionError):
                fail("MATERIAL_QUANTITY_UNRESOLVED", demand.spec_id, "单位、数量或定性份额缺少依据")
    known_specs = {spec.spec_id for spec in recipe.material_specs}
    for spec in recipe.material_specs:
        if not set(spec.composition) <= known_specs:
            fail("MATERIAL_LINEAGE_MISSING", spec.spec_id, "物料组成引用不存在")
    return tuple(violations)
