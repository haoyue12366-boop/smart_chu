"""离线模型调用、失败归档与草稿解析，不发布知识或生成审核记录。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from app.domain.extraction import (
    ExtractionRequest,
    ExtractionResponse,
    ExtractionRun,
    ExtractionVersions,
    ProviderResult,
)
from app.domain.ports import ExtractionAdapter, ExtractionStore
from app.llm.extraction_contract import (
    PARSER_VERSION,
    canonical_schema_bytes,
    parse_extraction,
    strict_json,
    validate_request_body,
)


class ArchivedLLMProvider:
    def __init__(
        self,
        adapter: ExtractionAdapter,
        archive: ExtractionStore,
        versions: ExtractionVersions,
        *,
        timeout_sec: float = 30,
    ) -> None:
        if timeout_sec <= 0:
            raise ValueError("抽取总超时必须为正数")
        if versions.parser_version != PARSER_VERSION:
            raise ValueError("不支持的抽取解析器版本")
        self.adapter = adapter
        self.archive = archive
        self.versions = versions
        self.timeout_sec = timeout_sec

    async def extract(self, request: ExtractionRequest) -> ExtractionResponse:
        self.archive.read(request.source)
        rendered = self.archive.read(request.rendered_request)
        schema = self.archive.read(request.output_schema)
        if strict_json(schema) != strict_json(canonical_schema_bytes()):
            raise ValueError("归档输出 Schema 与当前解析器不一致")
        validate_request_body(request, rendered)
        called_at = datetime.now(UTC)
        try:
            async with asyncio.timeout(self.timeout_sec):
                result = await self.adapter.generate(rendered, self.timeout_sec)
        except TimeoutError:
            result = ProviderResult(status="FAILED", error_message="TRANSPORT_TIMEOUT")
        raw = (
            self.archive.put(result.raw_response, "application/json")
            if result.raw_response is not None
            else None
        )
        structured = None
        status, error = result.status, result.error_message
        if status == "SUCCEEDED" and result.output_json is not None:
            try:
                recipe = parse_extraction(result.output_json, request.source_recipe_id)
                structured = self.archive.put(recipe.model_dump_json().encode(), "application/json")
            except ValueError:
                status, error = "FAILED", "INVALID_EXTRACTION"
        run = ExtractionRun(
            **self.versions.model_dump(),
            source_recipe_id=request.source_recipe_id,
            source=request.source,
            rendered_request=request.rendered_request,
            output_schema=request.output_schema,
            requested_model=request.requested_model,
            generation_parameters=request.generation_parameters,
            provider=self.adapter.provider_name,
            resolved_model_revision=result.resolved_model_revision,
            returned_model=result.returned_model,
            provider_request_id=result.provider_request_id,
            called_at=called_at,
            status=status,
            raw_response=raw,
            response_profile=(
                self.archive.put(result.response_profile_json.encode(), "application/json")
                if result.response_profile_json is not None
                else None
            ),
            structured_result=structured,
            finish_reason=result.finish_reason,
            error_message=error,
        )
        self.archive.save_run(run)
        return ExtractionResponse(
            run_id=run.run_id,
            status=status,
            raw_response=raw,
            structured_result=structured,
            error_message=error,
        )
