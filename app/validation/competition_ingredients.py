"""从绑定菜谱独立核对采购数量；不调用 Adapter 的分类或汇总函数。"""

from collections import Counter, defaultdict

from app.domain.canonical_recipe import CanonicalRecipeModel
from app.domain.competition_contract import IngredientGroup, IngredientItem
from app.domain.material import MaterialRequirement
from app.domain.quantity import ScaledQuantity


def requirement_label(requirement: MaterialRequirement) -> str:
    low = requirement.quantity
    if low is None:
        return requirement.qualitative_quantity or "原配方一批"
    upper = requirement.upper_quantity
    if upper is None:
        return format(low.as_decimal().normalize(), "f") + low.unit
    high = upper.convert(low.unit, low.scale)
    return (
        format(low.as_decimal().normalize(), "f")
        + "～"
        + format(high.as_decimal().normalize(), "f")
        + low.unit
    )


def check_recipe_ingredients(
    items: tuple[IngredientItem, ...], recipe: CanonicalRecipeModel
) -> None:
    specs = {s.spec_id: s.name for s in recipe.material_specs}
    expected = Counter(
        (specs[req.spec_id], requirement_label(req)) for req in recipe.ingredient_requirements
    )
    if Counter((item.name, item.unit) for item in items) != expected:
        raise ValueError("菜谱采购原料、数量或范围与绑定配方不一致")


def check_ingredient_summary(
    groups: tuple[IngredientGroup, ...], recipes: tuple[CanonicalRecipeModel, ...]
) -> None:
    quantities: dict[str, list[ScaledQuantity]] = defaultdict(list)
    labels: dict[str, list[str]] = defaultdict(list)
    for recipe in recipes:
        specs = {s.spec_id: s.name for s in recipe.material_specs}
        for req in recipe.ingredient_requirements:
            name = specs[req.spec_id]
            if req.exact_quantity is None:
                label = requirement_label(req)
                if label not in labels[name]:
                    labels[name].append(label)
                continue
            amount = req.exact_quantity
            for index, total in enumerate(quantities[name]):
                try:
                    combined = total.add(amount)
                except ValueError:
                    continue
                quantities[name][index] = combined
                break
            else:
                quantities[name].append(amount)
    expected = Counter(
        (
            name,
            " + ".join(
                [
                    format(value.as_decimal().normalize(), "f") + value.unit
                    for value in quantities[name]
                ]
                + labels[name]
            ),
        )
        for name in quantities.keys() | labels.keys()
    )
    actual = Counter((item.name, item.unit) for group in groups for item in group.list)
    if actual != expected or len({g.type for g in groups}) != len(groups):
        raise ValueError("采购汇总漏项、重复或改变原配方数量")
