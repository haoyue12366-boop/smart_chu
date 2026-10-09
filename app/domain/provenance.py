from __future__ import annotations

import hashlib
import os
from enum import StrEnum
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal, Self

from pydantic import Field, model_validator

from app.domain.base import Digest, FrozenModel, NonEmpty, PositiveInt, ReviewStatus


def resolve_artifact_path(path: Path) -> Path:
    """统一 Windows 普通与长路径表示，之后再比较已解析的目录边界。"""
    resolved = path.resolve()
    text = str(resolved)
    if os.name == "nt" and text.startswith("\\\\?\\"):
        text = "\\\\" + text[8:] if text.startswith("\\\\?\\UNC\\") else text[4:]
        return Path(text)
    return resolved


class ArtifactRef(FrozenModel):
    path: NonEmpty
    sha256: Digest
    media_type: NonEmpty

    @model_validator(mode="after")
    def safe_relative_path(self) -> Self:
        posix, windows = PurePosixPath(self.path), PureWindowsPath(self.path)
        if (
            posix.is_absolute()
            or windows.drive
            or windows.root
            or ".." in posix.parts
            or ".." in windows.parts
            or "\\" in self.path
        ):
            raise ValueError("内容引用必须为归档根目录下的相对 POSIX 路径")
        return self

    def verify(self, root: Path) -> Path:
        path = resolve_artifact_path(root / self.path)
        if not path.is_relative_to(resolve_artifact_path(root)) or not path.is_file():
            raise ValueError(f"内容引用不存在或越界：{self.path}")
        if hashlib.sha256(path.read_bytes()).hexdigest() != self.sha256:
            raise ValueError(f"内容哈希不匹配：{self.path}")
        return path


class ProvenanceOrigin(StrEnum):
    SOURCE_EXPLICIT = "SOURCE_EXPLICIT"
    SOURCE_DERIVED = "SOURCE_DERIVED"
    HUMAN_ESTIMATE = "HUMAN_ESTIMATE"
    HUMAN_MEASURED = "HUMAN_MEASURED"
    EXTERNAL_EVIDENCE = "EXTERNAL_EVIDENCE"
    MODEL_SUGGESTION = "MODEL_SUGGESTION"


class ProvenanceRecord(FrozenModel):
    provenance_id: NonEmpty
    origin: ProvenanceOrigin
    source_file: NonEmpty
    source_hash: Digest
    record_id: NonEmpty
    field_path: NonEmpty
    step_number: PositiveInt | None = None
    text_span: NonEmpty
    artifact_ref: ArtifactRef | None = None
    extraction_run_id: NonEmpty | None = None
    review_status: ReviewStatus = ReviewStatus.NEEDS_REVIEW
    approved_review_ref: NonEmpty | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    confidence_kind: Literal["HEURISTIC", "CALIBRATED_PROBABILITY"] | None = None

    @model_validator(mode="after")
    def authority(self) -> Self:
        if self.confidence is not None and self.confidence_kind is None:
            raise ValueError("置信数值必须说明口径")
        if self.review_status == ReviewStatus.APPROVED and not self.approved_review_ref:
            raise ValueError("置信评分不能替代人工审核记录")
        return self
