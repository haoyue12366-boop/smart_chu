"""在线只读知识事实；语义哈希不包含构建时间或哈希自身。"""

from typing import Literal, Self

from pydantic import AwareDatetime, model_validator

from app.domain.base import Digest, FrozenModel, NonEmpty, content_hash
from app.domain.knowledge_release import ReviewedRelease


class SchedulingKnowledgeSnapshot(FrozenModel):
    snapshot_schema_version: Literal["1.0"] = "1.0"
    exporter_version: Literal["snapshot-export-v1", "snapshot-canonical-v1"] = "snapshot-export-v1"
    snapshot_id: NonEmpty
    content_hash: Digest
    build_time: AwareDatetime
    knowledge: ReviewedRelease

    @model_validator(mode="after")
    def consistent_hash(self) -> Self:
        digest = content_hash(self.knowledge)
        if self.content_hash != digest or self.snapshot_id != "snapshot-" + digest:
            raise ValueError("快照语义哈希或身份不匹配")
        return self
