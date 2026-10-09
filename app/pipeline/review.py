"""审核者提供的明确补丁与证据一同归档；不自动完成审核。"""

from app.domain.base import FrozenModel, NonEmpty
from app.domain.canonical_recipe import CanonicalRecipeModel
from app.domain.ports import ExtractionStore
from app.domain.provenance import ArtifactRef
from app.domain.review import ReviewPatch, apply_review_patch


class ReviewEvidenceRef(FrozenModel):
    evidence_id: NonEmpty
    artifact: ArtifactRef


class ArchivedReview(FrozenModel):
    schema_version: str = "1.0"
    patch: ReviewPatch
    evidence: tuple[ReviewEvidenceRef, ...]


class ReviewApplication(FrozenModel):
    recipe: CanonicalRecipeModel
    patch_ref: ArtifactRef
    recipe_ref: ArtifactRef


def load_archived_review(archive: ExtractionStore, ref: ArtifactRef) -> ReviewPatch:
    record = ArchivedReview.model_validate_json(archive.read(ref))
    if record.schema_version != "1.0":
        raise ValueError("不支持的审核归档 Schema")
    evidence = {entry.evidence_id: entry.artifact for entry in record.evidence}
    if (
        len(evidence) != len(record.evidence)
        or not set(record.patch.evidence_refs) <= evidence.keys()
    ):
        raise ValueError("审核依据不完整或身份重复")
    for artifact in evidence.values():
        archive.read(artifact)
    return record.patch


def apply_archived_review(
    recipe: CanonicalRecipeModel,
    patch: ReviewPatch,
    archive: ExtractionStore,
    evidence: dict[str, ArtifactRef],
) -> ReviewApplication:
    if not patch.evidence_refs or not set(patch.evidence_refs) <= evidence.keys():
        raise ValueError("审核依据缺失")
    for key in patch.evidence_refs:
        archive.read(evidence[key])
    updated = apply_review_patch(recipe, patch)
    record = ArchivedReview(
        patch=patch,
        evidence=tuple(
            ReviewEvidenceRef(evidence_id=key, artifact=evidence[key])
            for key in sorted(set(patch.evidence_refs))
        ),
    )
    patch_ref = archive.put(record.model_dump_json().encode(), "application/json")
    recipe_ref = archive.put(updated.model_dump_json().encode(), "application/json")
    return ReviewApplication(recipe=updated, patch_ref=patch_ref, recipe_ref=recipe_ref)
