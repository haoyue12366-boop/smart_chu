"""快照校验证据契约，由离线导出及只读加载共同核对。"""

from typing import Literal

from pydantic import Field

from app.domain.base import Digest, FrozenModel, NonEmpty


class SnapshotValidationReport(FrozenModel):
    valid: bool
    release_id: NonEmpty
    canonical_hash: Digest
    snapshot_hash: Digest
    graph_hash: Digest
    source_kind: Literal["GRAPH", "CANONICAL"] = Field(
        default="GRAPH", exclude_if=lambda value: value == "GRAPH"
    )
