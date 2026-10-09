"""验证原始响应与审核链，收集可独立重放的归档闭包；不发外部请求。"""

from pathlib import Path

from pydantic import JsonValue, TypeAdapter

from app.domain.knowledge_release import ReviewedRelease
from app.domain.provenance import ArtifactRef
from app.domain.release_replay import ReleaseReplay
from app.pipeline.extraction_archive import ExtractionArchive
from app.pipeline.replay import replay_extraction

JSON: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)


def collect_replay_artifacts(
    source: ReviewedRelease, replays: tuple[ReleaseReplay, ...], archive_root: Path | None
) -> dict[ArtifactRef, bytes]:
    required = {p.extraction_run_id for p in source.provenance if p.extraction_run_id}
    if len({r.run_id for r in replays}) != len(replays) or required != {r.run_id for r in replays}:
        raise ValueError("发布抽取引用与重放清单不完整或重复")
    if not replays:
        return {}
    if archive_root is None:
        raise ValueError("发布缺少抽取归档目录")
    archive = ExtractionArchive(archive_root)
    files: dict[ArtifactRef, bytes] = {}

    def visit(value: JsonValue) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            if set(value) == {"path", "sha256", "media_type"}:
                ref = ArtifactRef.model_validate(value)
                if ref in files:
                    return
                payload = archive.read(ref)
                files[ref] = payload
                if ref.media_type == "application/json":
                    visit(JSON.validate_json(payload))
            else:
                for item in value.values():
                    visit(item)

    recipes = {r.recipe_id: r for r in source.recipes}
    for binding in replays:
        if binding.run_manifest.path != f"runs/{binding.run_id}.json":
            raise ValueError("抽取清单路径与调用身份不一致")
        visit(JSON.validate_json(binding.model_dump_json()))
        report = replay_extraction(
            binding.run_id,
            binding.versions,
            archive,
            review_patch_refs=binding.review_patch_refs,
            normalization_ref=binding.normalization_ref,
            expected_content_hash=binding.expected_content_hash,
        )
        if recipes.get(report.recipe.recipe_id) != report.recipe:
            raise ValueError("归档重放结果与本次发布菜谱不一致")
    return files
