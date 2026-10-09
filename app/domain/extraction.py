from __future__ import annotations

from pathlib import Path
from typing import Literal, Self
from uuid import uuid4

from pydantic import AwareDatetime, Field, model_validator

from app.domain.base import Digest, FrozenModel, NonEmpty
from app.domain.ids import RecipeId
from app.domain.provenance import ArtifactRef


class ExtractionVersions(FrozenModel):
    prompt_version: NonEmpty
    schema_version: NonEmpty
    parser_version: NonEmpty
    normalizer_version: NonEmpty
    rule_version: NonEmpty
    code_revision: NonEmpty
    dependency_lock_hash: Digest


class ProviderResult(FrozenModel):
    status: Literal["SUCCEEDED", "FAILED", "REFUSED", "TRUNCATED"]
    raw_response: bytes | None = None
    output_json: str | None = None
    error_message: str | None = None
    finish_reason: str | None = None
    provider_request_id: str | None = None
    returned_model: str | None = None
    resolved_model_revision: NonEmpty = "unknown"
    response_profile_json: str | None = None

    @model_validator(mode="after")
    def outcome(self) -> Self:
        if self.status == "SUCCEEDED" and (self.raw_response is None or self.output_json is None):
            raise ValueError("成功供应商返回必须包含原始内容和结构化文本")
        if self.status != "SUCCEEDED" and not self.error_message:
            raise ValueError("失败供应商返回必须包含安全错误码")
        return self


class GenerationParameter(FrozenModel):
    name: NonEmpty
    value_json: str
    applicability: Literal["SUBMITTED", "NOT_SUPPORTED"] = "SUBMITTED"


class ExtractionRequest(FrozenModel):
    source_recipe_id: RecipeId
    source: ArtifactRef
    rendered_request: ArtifactRef
    output_schema: ArtifactRef
    requested_model: NonEmpty
    generation_parameters: tuple[GenerationParameter, ...] = ()


class ExtractionResponse(FrozenModel):
    run_id: NonEmpty
    status: Literal["SUCCEEDED", "FAILED", "REFUSED", "TRUNCATED"]
    raw_response: ArtifactRef | None
    structured_result: ArtifactRef | None = None
    error_message: str | None = None


class ExtractionRun(FrozenModel):
    run_id: NonEmpty = Field(default_factory=lambda: str(uuid4()))
    source_recipe_id: RecipeId
    source: ArtifactRef
    source_spans: tuple[NonEmpty, ...] = ()
    rendered_request: ArtifactRef
    output_schema: ArtifactRef
    raw_response: ArtifactRef | None = None
    response_profile: ArtifactRef | None = None
    structured_result: ArtifactRef | None = None
    provider: NonEmpty
    requested_model: NonEmpty
    resolved_model_revision: NonEmpty = "unknown"
    provider_request_id: str | None = None
    returned_model: str | None = None
    generation_parameters: tuple[GenerationParameter, ...] = ()
    called_at: AwareDatetime
    status: Literal["SUCCEEDED", "FAILED", "REFUSED", "TRUNCATED"]
    finish_reason: str | None = None
    error_message: str | None = None
    prompt_version: NonEmpty
    schema_version: NonEmpty
    parser_version: NonEmpty
    normalizer_version: NonEmpty
    rule_version: NonEmpty
    code_revision: NonEmpty
    dependency_lock_hash: Digest
    review_patch_refs: tuple[ArtifactRef, ...] = ()
    knowledge_release_id: NonEmpty | None = None
    snapshot_id: NonEmpty | None = None
    provenance_refs: tuple[NonEmpty, ...] = ()

    @model_validator(mode="after")
    def outcome_evidence(self) -> Self:
        if self.status == "SUCCEEDED" and (
            self.raw_response is None or self.structured_result is None
        ):
            raise ValueError("成功抽取必须保存原始返回与结构化结果")
        if self.status != "SUCCEEDED" and self.raw_response is None and not self.error_message:
            raise ValueError("失败调用必须保留错误或原始返回")
        return self

    def verify_artifacts(self, root: Path) -> None:
        for ref in (
            self.source,
            self.rendered_request,
            self.output_schema,
            self.raw_response,
            self.response_profile,
            self.structured_result,
            *self.review_patch_refs,
        ):
            if ref is not None:
                ref.verify(root)
