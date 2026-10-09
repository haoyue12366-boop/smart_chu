"""固定解析器只接受当前菜谱草稿，不赋予模型任何人工审核权限。"""

from __future__ import annotations

import json

from pydantic import JsonValue, TypeAdapter

from app.domain.base import ReviewStatus
from app.domain.canonical_recipe import CanonicalRecipeModel
from app.domain.extraction import ExtractionRequest, GenerationParameter
from app.domain.ids import RecipeId

PARSER_VERSION = "canonical-v1"
JSON_VALUE: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)


def _reject_constant(value: str) -> None:
    raise ValueError(f"JSON 不允许非有限数值：{value}")


def _unique_keys(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON 键重复")
        result[key] = value
    return result


def strict_json(payload: str | bytes) -> JsonValue:
    return JSON_VALUE.validate_python(
        json.loads(payload, parse_constant=_reject_constant, object_pairs_hook=_unique_keys)
    )


def canonical_schema_bytes() -> bytes:
    return json.dumps(
        CanonicalRecipeModel.model_json_schema(), ensure_ascii=False, sort_keys=True
    ).encode()


def validate_request_body(request: ExtractionRequest, body: bytes) -> None:
    validate_rendered_body(request.requested_model, request.generation_parameters, body)


def validate_rendered_body(
    requested_model: str, parameters: tuple[GenerationParameter, ...], body: bytes
) -> None:
    value = strict_json(body)
    if not isinstance(value, dict) or value.get("model") != requested_model:
        raise ValueError("请求中的实际模型与归档模型不一致")
    names: set[str] = set()
    for parameter in parameters:
        if parameter.name in names:
            raise ValueError("生成参数名称重复")
        names.add(parameter.name)
        expected = strict_json(parameter.value_json)
        if parameter.applicability == "SUBMITTED":
            if parameter.name not in value or value[parameter.name] != expected:
                raise ValueError("生成参数与实际发送请求不一致")
        elif parameter.name in value:
            raise ValueError("声明不支持的参数不能实际提交")
    sensitive = {"api_key", "apikey", "authorization", "password", "access_token"}

    def check(item: JsonValue) -> None:
        if isinstance(item, dict):
            if any(key.casefold() in sensitive for key in item):
                raise ValueError("凭据应在适配器请求头中配置，不能写入归档正文")
            for child in item.values():
                check(child)
        elif isinstance(item, list):
            for child in item:
                check(child)

    check(value)


def parse_extraction(output_json: str, source_recipe_id: RecipeId) -> CanonicalRecipeModel:
    recipe = CanonicalRecipeModel.model_validate(strict_json(output_json))
    if recipe.recipe_id != source_recipe_id:
        raise ValueError("模型改变了来源菜谱身份")
    if (
        recipe.approval is not None
        or recipe.review_status in {ReviewStatus.APPROVED, ReviewStatus.RETIRED}
        or recipe.review_patch_refs
        or any(op.review_status == ReviewStatus.APPROVED for op in recipe.operations)
    ):
        raise ValueError("模型输出不能携带人工批准、退役或审核补丁记录")
    payload = recipe.model_dump(mode="json")
    payload["review_status"] = "NEEDS_REVIEW"
    for operation in payload["operations"]:
        operation["review_status"] = "NEEDS_REVIEW"
    return CanonicalRecipeModel.model_validate(payload)
