"""JSON HTTP 适配器；响应路径和终止标识由供应商配置明确给出。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping

import httpx
from pydantic import JsonValue, model_validator

from app.domain.base import FrozenModel, NonEmpty
from app.domain.extraction import ProviderResult
from app.llm.extraction_contract import strict_json

JsonPath = tuple[str | int, ...]


class JsonResponseProfile(FrozenModel):
    output_path: JsonPath
    finish_reason_path: JsonPath | None = None
    success_reasons: tuple[NonEmpty, ...] = ()
    truncation_reasons: tuple[NonEmpty, ...] = ()
    refusal_reasons: tuple[NonEmpty, ...] = ()
    model_path: JsonPath | None = None
    revision_path: JsonPath | None = None
    request_id_path: JsonPath | None = None

    @model_validator(mode="after")
    def distinct_statuses(self) -> JsonResponseProfile:
        reasons = (*self.success_reasons, *self.truncation_reasons, *self.refusal_reasons)
        if len(reasons) != len(set(reasons)):
            raise ValueError("供应商终止标识不能重复或跨状态复用")
        if self.finish_reason_path is not None and not self.success_reasons:
            raise ValueError("指定终止字段时必须明确成功标识")
        return self


def _at(document: JsonValue, path: JsonPath) -> JsonValue:
    value = document
    for key in path:
        if isinstance(value, dict) and isinstance(key, str):
            value = value[key]
        elif isinstance(value, list) and isinstance(key, int) and key >= 0:
            value = value[key]
        else:
            raise ValueError("供应商字段路径不匹配")
    return value


def _optional_text(document: JsonValue, path: JsonPath | None) -> str | None:
    if path is None:
        return None
    try:
        value = _at(document, path)
    except (ValueError, KeyError, IndexError):
        return None
    return value if isinstance(value, str) and value else None


def decode_response(raw: bytes, profile: JsonResponseProfile) -> ProviderResult:
    try:
        document = strict_json(raw)
    except (ValueError, UnicodeError):
        return ProviderResult(
            status="FAILED",
            raw_response=raw,
            error_message="INVALID_PROVIDER_JSON",
            response_profile_json=profile.model_dump_json(),
        )
    finish = _optional_text(document, profile.finish_reason_path)
    metadata = {
        "finish_reason": finish,
        "provider_request_id": _optional_text(document, profile.request_id_path),
        "returned_model": _optional_text(document, profile.model_path),
        "resolved_model_revision": _optional_text(document, profile.revision_path) or "unknown",
        "response_profile_json": profile.model_dump_json(),
    }
    status, error = "SUCCEEDED", None
    if finish in profile.truncation_reasons:
        status, error = "TRUNCATED", "PROVIDER_TRUNCATED"
    elif finish in profile.refusal_reasons:
        status, error = "REFUSED", "PROVIDER_REFUSED"
    elif profile.finish_reason_path is not None and finish not in profile.success_reasons:
        status, error = "FAILED", "UNKNOWN_FINISH_REASON"
    output = None
    if error is None:
        try:
            value = _at(document, profile.output_path)
            output = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        except (ValueError, KeyError, IndexError):
            status, error = "FAILED", "INVALID_PROVIDER_ENVELOPE"
    return ProviderResult.model_validate(
        {
            "status": status,
            "raw_response": raw,
            "output_json": output,
            "error_message": error,
            **metadata,
        }
    )


class JsonHttpAdapter:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        endpoint: str,
        provider_name: str,
        profile: JsonResponseProfile,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.client = client
        self.endpoint = endpoint
        self.provider_name = provider_name
        self.profile = profile
        self._headers = dict(headers or {})

    async def generate(self, rendered_request: bytes, timeout_sec: float) -> ProviderResult:
        try:
            async with asyncio.timeout(timeout_sec):
                response = await self.client.post(
                    self.endpoint,
                    content=rendered_request,
                    headers={"Content-Type": "application/json", **self._headers},
                    timeout=timeout_sec,
                    follow_redirects=False,
                )
        except (TimeoutError, httpx.TimeoutException):
            return ProviderResult(status="FAILED", error_message="TRANSPORT_TIMEOUT")
        except httpx.HTTPError:
            return ProviderResult(status="FAILED", error_message="TRANSPORT_ERROR")
        if not response.is_success:
            return ProviderResult(
                status="FAILED",
                raw_response=response.content,
                error_message=f"HTTP_STATUS_{response.status_code}",
            )
        return decode_response(response.content, self.profile)
