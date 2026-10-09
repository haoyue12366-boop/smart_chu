"""严格数值与不可变模型公共基础。"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

MAX_INT = 2**60
NonEmpty = Annotated[str, Field(strict=True, min_length=1, pattern=r"\S")]
NonNegativeInt = Annotated[int, Field(strict=True, ge=0, le=MAX_INT)]
PositiveInt = Annotated[int, Field(strict=True, gt=0, le=MAX_INT)]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class FrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, validate_default=True, revalidate_instances="always"
    )


class ReviewStatus(StrEnum):
    DRAFT = "DRAFT"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    APPROVED = "APPROVED"
    RETIRED = "RETIRED"


def content_hash(value: BaseModel) -> str:
    """仅对调用者指定的语义模型求哈希，不包含额外生成时间。"""
    encoded = json.dumps(
        value.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
