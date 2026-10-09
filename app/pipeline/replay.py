"""仅重放已归档响应和明确补丁；本地重放不会重新请求外部模型。"""

from __future__ import annotations

from app.domain.base import Digest, FrozenModel, NonEmpty
from app.domain.canonical_recipe import CanonicalRecipeModel
from app.domain.extraction import ExtractionVersions
from app.domain.ports import ExtractionStore
from app.domain.provenance import ArtifactRef
from app.domain.review import apply_review_patch
from app.llm.extraction_contract import PARSER_VERSION, parse_extraction
from app.llm.provider_adapter import JsonResponseProfile, decode_response
from app.pipeline.normalize import NORMALIZER_VERSION, NormalizationEvidence, normalize
from app.pipeline.review import load_archived_review


class ReplayReport(FrozenModel):
    run_id: NonEmpty
    kind: str = "LOCAL_REPLAY"
    recipe: CanonicalRecipeModel
    reproduced_extraction_hash: Digest
    replayed_content_hash: Digest
    applied_patch_refs: tuple[ArtifactRef, ...] = ()
    external_calls: int = 0


def replay_extraction(
    run_id: str,
    versions: ExtractionVersions,
    archive: ExtractionStore,
    *,
    review_patch_refs: tuple[ArtifactRef, ...] = (),
    normalization_ref: ArtifactRef | None = None,
    expected_content_hash: str | None = None,
) -> ReplayReport:
    run = archive.load_run(run_id)
    if any(getattr(run, key) != value for key, value in versions.model_dump().items()):
        raise ValueError("重放版本与原调用归档不一致")
    if versions.parser_version != PARSER_VERSION:
        raise ValueError("不支持的解析器版本")
    if run.status != "SUCCEEDED" or run.raw_response is None or run.structured_result is None:
        raise ValueError("调用没有可重放的成功草稿")
    if run.response_profile is None:
        raise ValueError("缺少原始响应解码契约，不能伪称可重放")
    profile = JsonResponseProfile.model_validate_json(archive.read(run.response_profile))
    response = decode_response(archive.read(run.raw_response), profile)
    if response.status != "SUCCEEDED" or response.output_json is None:
        raise ValueError("原始响应无法重新解码")
    recipe = parse_extraction(response.output_json, run.source_recipe_id)
    saved = CanonicalRecipeModel.model_validate_json(archive.read(run.structured_result))
    if recipe != saved:
        raise ValueError("原始返回重放与存档草稿不一致")
    initial_hash = recipe.document_hash
    if normalization_ref is not None:
        normalization = NormalizationEvidence.model_validate_json(archive.read(normalization_ref))
        if normalization.normalizer_version != run.normalizer_version or (
            normalization.normalizer_version != NORMALIZER_VERSION
        ):
            raise ValueError("归档规范化器版本不匹配")
        recipe = normalize(recipe, normalization).recipe
    patches = (*run.review_patch_refs, *review_patch_refs)
    for ref in patches:
        patch = load_archived_review(archive, ref)
        recipe = apply_review_patch(recipe, patch)
    if expected_content_hash is not None and recipe.document_hash != expected_content_hash:
        raise ValueError("重放结果与指定内容哈希不一致，可能缺少审核补丁")
    return ReplayReport(
        run_id=run_id,
        recipe=recipe,
        reproduced_extraction_hash=initial_hash,
        replayed_content_hash=recipe.document_hash,
        applied_patch_refs=patches,
    )
