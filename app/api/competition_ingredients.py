"""采购食材来自会话绑定的规范菜谱，汇总不重复计算共享加工。"""

from collections import defaultdict
from typing import Literal

from app.domain.canonical_recipe import CanonicalRecipeModel
from app.domain.competition_contract import IngredientGroup, IngredientItem
from app.domain.material import MaterialRequirement
from app.domain.quantity import ScaledQuantity

Category = Literal["荤菜", "素菜", "调味品"]
_SEASONING = (
    "盐",
    "糖",
    "油",
    "酱",
    "醋",
    "酒",
    "胡椒",
    "香料",
    "孜然",
    "五香",
    "蜂蜜",
    "淀粉",
    "生粉",
)
_ANIMAL = (
    "肉",
    "鸡",
    "鸭",
    "鹅",
    "牛排",
    "猪",
    "羊",
    "鱼",
    "虾",
    "蟹",
    "贝",
    "蛤",
    "蛋",
    "奶",
    "乳",
    "黄油",
    "芝士",
    "干酪",
    "火腿",
)


def category(name: str) -> Category:
    if any(term in name for term in _SEASONING):
        return "调味品"
    return "荤菜" if any(term in name for term in _ANIMAL) else "素菜"


def amount_text(requirement: MaterialRequirement) -> str:
    if requirement.quantity is not None:
        value = requirement.quantity.as_decimal()
        text = format(value.normalize(), "f") + requirement.quantity.unit
        if requirement.upper_quantity is not None:
            upper = requirement.upper_quantity.convert(
                requirement.quantity.unit, requirement.quantity.scale
            )
            text = (
                format(value.normalize(), "f")
                + "～"
                + format(upper.as_decimal().normalize(), "f")
                + upper.unit
            )
        return text
    return requirement.qualitative_quantity or "原配方一批"


def recipe_ingredients(
    recipe: CanonicalRecipeModel,
) -> tuple[tuple[IngredientItem, ...], tuple[IngredientItem, ...]]:
    specs = {spec.spec_id: spec for spec in recipe.material_specs}
    major: list[IngredientItem] = []
    minor: list[IngredientItem] = []
    for requirement in recipe.ingredient_requirements:
        name = specs[requirement.spec_id].name
        item = IngredientItem(name=name, unit=amount_text(requirement))
        (minor if category(name) == "调味品" else major).append(item)
    return tuple(major), tuple(minor)


def ingredient_summary(recipes: tuple[CanonicalRecipeModel, ...]) -> tuple[IngredientGroup, ...]:
    exact: dict[str, list[ScaledQuantity]] = defaultdict(list)
    labels: dict[str, list[str]] = defaultdict(list)
    for recipe in recipes:
        specs = {spec.spec_id: spec for spec in recipe.material_specs}
        for requirement in recipe.ingredient_requirements:
            name = specs[requirement.spec_id].name
            if requirement.quantity_kind == "EXACT" and requirement.quantity is not None:
                values = exact[name]
                for index, old in enumerate(values):
                    try:
                        values[index] = old.add(requirement.quantity)
                        break
                    except ValueError:
                        continue
                else:
                    values.append(requirement.quantity)
            else:
                labels[name].append(amount_text(requirement))
    groups: dict[Category, list[IngredientItem]] = {"荤菜": [], "素菜": [], "调味品": []}
    for name in sorted(exact.keys() | labels.keys()):
        amounts = [
            format(value.as_decimal().normalize(), "f") + value.unit for value in exact[name]
        ]
        amounts.extend(dict.fromkeys(labels[name]))
        groups[category(name)].append(IngredientItem(name=name, unit=" + ".join(amounts)))
    return tuple(
        IngredientGroup(type=kind, list=tuple(items)) for kind, items in groups.items() if items
    )
