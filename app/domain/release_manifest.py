"""磁盘发布清单；哈希引用整个发布，不依赖可变的 active 指针。"""

from typing import Literal

from app.domain.base import Digest, FrozenModel, NonEmpty
from app.domain.provenance import ArtifactRef
from app.domain.release_replay import ReleaseReplay


class ReleaseManifest(FrozenModel):
    schema_version: Literal["1.0"] = "1.0"
    release_id: NonEmpty
    knowledge_version: NonEmpty
    rule_version: NonEmpty
    snapshot_id: NonEmpty
    snapshot_hash: Digest
    release_kind: Literal["development", "sample", "competition"]
    artifacts: tuple[ArtifactRef, ...]
    replays: tuple[ReleaseReplay, ...] = ()
