"""发布所需的本地抽取及审核重放绑定。"""

from app.domain.base import Digest, FrozenModel, NonEmpty
from app.domain.extraction import ExtractionVersions
from app.domain.provenance import ArtifactRef


class ReleaseReplay(FrozenModel):
    run_id: NonEmpty
    run_manifest: ArtifactRef
    versions: ExtractionVersions
    expected_content_hash: Digest
    review_patch_refs: tuple[ArtifactRef, ...] = ()
    normalization_ref: ArtifactRef | None = None
