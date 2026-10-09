"""应用明确审核补丁；不推断缺失工艺，不执行人工审核本身。"""

from __future__ import annotations

import json
from typing import Literal, Self

from pydantic import AwareDatetime, JsonValue, model_validator

from app.domain.base import Digest, FrozenModel, NonEmpty
from app.domain.canonical_recipe import CanonicalRecipeModel, ReviewStamp
from app.domain.ids import RecipeId


class FieldChange(FrozenModel):
    path: NonEmpty
    before_json: str
    after_json: str

    @model_validator(mode="after")
    def json_values(self) -> Self:
        for value in (self.before_json, self.after_json):
            json.loads(
                value,
                parse_constant=lambda _: (_ for _ in ()).throw(
                    ValueError("不允许非有限 JSON 数值")
                ),
            )
        return self


class ReviewPatch(FrozenModel):
    patch_id: NonEmpty
    recipe_id: RecipeId
    base_recipe_version: NonEmpty
    base_content_hash: Digest
    action: Literal["MODIFY", "APPROVE", "RETIRE"]
    actor_kind: Literal["HUMAN", "MODEL"]
    reviewer: NonEmpty
    reviewed_at: AwareDatetime
    reason: NonEmpty
    evidence_refs: tuple[NonEmpty, ...]
    next_recipe_version: NonEmpty | None = None
    changes: tuple[FieldChange, ...] = ()

    @model_validator(mode="after")
    def authorization(self) -> Self:
        if self.action == "APPROVE" and (
            self.actor_kind != "HUMAN" or not self.evidence_refs or self.changes
        ):
            raise ValueError("批准需要明确人工审核者和证据，不能夹带未经审核的修改")
        if self.action == "MODIFY" and (
            not self.changes
            or self.next_recipe_version is None
            or self.next_recipe_version == self.base_recipe_version
        ):
            raise ValueError("修改必须有字段变更及新的菜谱版本")
        if self.action == "RETIRE" and (self.actor_kind != "HUMAN" or self.changes):
            raise ValueError("退役需要明确人工记录")
        return self


def _protected_values(value: JsonValue, path: str = "") -> list[tuple[str, JsonValue]]:
    result: list[tuple[str, JsonValue]] = []
    if isinstance(value, dict):
        for key, item in sorted(value.items()):
            if key in {"operation_id", "spec_id", "provenance_refs", "review_status", "approval"}:
                result.append((path + "/" + key, item))
            result.extend(_protected_values(item, path + "/" + key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            result.extend(_protected_values(item, path + "/" + str(index)))
    return result


def _replace_value(document: JsonValue, change: FieldChange) -> None:
    parts = change.path.split("/")[1:]
    if (
        not change.path.startswith("/")
        or not parts
        or parts[0]
        not in {"operations", "dependencies", "material_specs", "ingredient_requirements"}
    ):
        raise ValueError("审核补丁不能修改身份或审核元数据")
    if any(
        part in {"review_status", "approval", "provenance_refs", "operation_id", "spec_id"}
        for part in parts
    ):
        raise ValueError("补丁不允许覆盖来源、批准或稳定身份")
    current = document
    try:
        for part in parts[:-1]:
            if isinstance(current, list):
                if not part.isdecimal():
                    raise ValueError("数组索引无效")
                current = current[int(part)]
            elif isinstance(current, dict):
                current = current[part]
            else:
                raise ValueError("字段路径不指向容器")
        last = parts[-1]
        before = json.loads(change.before_json)
        after = json.loads(change.after_json)
        if _protected_values(before) != _protected_values(after):
            raise ValueError("容器替换不能隐式覆盖稳定身份、来源或审核状态")
        if isinstance(current, list) and last.isdecimal():
            if current[int(last)] != before:
                raise ValueError("补丁前值与当前内容不一致")
            current[int(last)] = after
        elif isinstance(current, dict) and last in current:
            if current[last] != before:
                raise ValueError("补丁前值与当前内容不一致")
            current[last] = after
        else:
            raise ValueError("补丁字段不存在")
    except (KeyError, IndexError) as exc:
        raise ValueError("补丁字段不存在") from exc


def apply_review_patch(recipe: CanonicalRecipeModel, patch: ReviewPatch) -> CanonicalRecipeModel:
    if (
        recipe.recipe_id != patch.recipe_id
        or recipe.recipe_version != patch.base_recipe_version
        or recipe.semantic_hash() != patch.base_content_hash
    ):
        raise ValueError("补丁基础版本或内容哈希不匹配")
    if patch.patch_id in recipe.review_patch_refs:
        raise ValueError("补丁身份已应用")
    document = recipe.model_dump(mode="json")
    document["review_patch_refs"] = [*recipe.review_patch_refs, patch.patch_id]
    if patch.action == "APPROVE":
        document["review_status"] = "APPROVED"
        document["approval"] = ReviewStamp(
            review_id=patch.patch_id,
            reviewer=patch.reviewer,
            reviewed_at=patch.reviewed_at,
            evidence_refs=patch.evidence_refs,
            approved_content_hash=recipe.semantic_hash(),
        ).model_dump(mode="json")
    elif patch.action == "MODIFY":
        for change in patch.changes:
            _replace_value(document, change)
        document["recipe_version"] = patch.next_recipe_version
        document["review_status"] = "NEEDS_REVIEW"
        document["approval"] = None
        for operation in document["operations"]:
            operation["review_status"] = "NEEDS_REVIEW"
    else:
        document["review_status"] = "RETIRED"
        document["approval"] = None
    return CanonicalRecipeModel.model_validate(document)
