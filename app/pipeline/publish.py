"""先完整构建并重载校验，再原子安装目录和激活指针；失败保留旧发布。"""

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.domain.knowledge import ReleaseRef
from app.domain.provenance import ArtifactRef
from app.domain.release_feasibility import ReleasePlanProof
from app.domain.release_replay import ReleaseReplay
from app.knowledge.index import validate_index
from app.knowledge.loader import load_release, read_release_ref, release_directory
from app.pipeline.release_manifest import encode, make_manifest, manifest_ref
from app.pipeline.release_replay import collect_replay_artifacts
from app.pipeline.snapshot_export import SnapshotBuildResult
from app.validation.knowledge import validate_knowledge


@dataclass(frozen=True)
class ReleaseBundle:
    build: SnapshotBuildResult
    source_root: Path
    replays: tuple[ReleaseReplay, ...] = ()
    archive_root: Path | None = None
    single_recipe_plans: tuple[ReleasePlanProof, ...] = ()


def write_file(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _activate(root: Path, ref: ReleaseRef) -> None:
    with tempfile.NamedTemporaryFile(dir=root, prefix=".active-", delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(encode(ref))
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.replace(temporary, root / "active_release.json")
    finally:
        temporary.unlink(missing_ok=True)


def publish_release(bundle: ReleaseBundle, root: Path) -> ReleaseRef:
    build = SnapshotBuildResult.model_validate(bundle.build)
    source = build.snapshot.knowledge
    target = release_directory(root, source.release_id)
    fresh = validate_knowledge(source.recipes, source.profiles, source.rules, source.scope)
    if fresh != source.validation or not fresh.valid:
        raise ValueError("发布知识校验不一致或不通过")
    if source.scope.release_kind == "competition":
        from app.pipeline.release_feasibility import validate_release_plans

        validate_release_plans(build, bundle.single_recipe_plans)
    if (
        not build.validation.valid
        or build.validation.release_id != source.release_id
        or build.validation.canonical_hash != source.content_hash
        or build.validation.snapshot_hash != build.snapshot.content_hash
    ):
        raise ValueError("快照导出校验报告与内容不一致")
    validate_index(source, build.index)
    for artifact in source.source_artifacts:
        artifact.verify(bundle.source_root)
    replay_files = collect_replay_artifacts(source, bundle.replays, bundle.archive_root)
    root.mkdir(parents=True, exist_ok=True)
    if target.exists():
        current = read_release_ref(root, source.release_id)
        existing = load_release(root, current)
        if existing.snapshot.knowledge != source or existing.manifest.replays != bundle.replays:
            raise ValueError("不可覆盖已存在的不同发布内容")
        _activate(root, current)
        return current
    # TemporaryDirectory 只清理自身生成的 staging；已安装的版本从不删除。
    with tempfile.TemporaryDirectory(dir=root, prefix=".staging-") as temp:
        staging_root = Path(temp)
        directory = staging_root / source.release_id
        payloads = {
            "snapshot.json": encode(build.snapshot),
            "index.json": encode(build.index),
            "snapshot_validation_report.json": encode(build.validation),
        }
        if source.scope.release_kind == "competition":
            from pydantic import TypeAdapter

            payloads["single_recipe_plans.json"] = TypeAdapter(
                tuple[ReleasePlanProof, ...]
            ).dump_json(bundle.single_recipe_plans, indent=2)
        refs = []
        for name, payload in payloads.items():
            write_file(directory / name, payload)
            refs.append(
                ArtifactRef(
                    path=name,
                    sha256=hashlib.sha256(payload).hexdigest(),
                    media_type="application/json",
                )
            )
        for artifact in source.source_artifacts:
            if artifact.path in payloads or artifact.path == "manifest.json":
                raise ValueError("来源路径与发布保留文件冲突")
            payload = artifact.verify(bundle.source_root).read_bytes()
            write_file(directory / artifact.path, payload)
            refs.append(artifact)
        for artifact, payload in replay_files.items():
            packaged = artifact.model_copy(update={"path": "extraction/" + artifact.path})
            write_file(directory / packaged.path, payload)
            refs.append(packaged)
        collect_replay_artifacts(source, bundle.replays, directory / "extraction")
        manifest = make_manifest(build.snapshot, tuple(refs), bundle.replays)
        write_file(directory / "manifest.json", encode(manifest))
        ref = manifest_ref(manifest)
        load_release(staging_root, ref)
        try:
            directory.rename(target)
        except OSError:
            if not target.exists():
                raise
            existing_ref = read_release_ref(root, source.release_id)
            installed = load_release(root, existing_ref)
            if (
                installed.snapshot.knowledge != source
                or installed.manifest.replays != bundle.replays
            ):
                raise ValueError("竞争发布的不可变身份冲突") from None
            ref = existing_ref
        _activate(root, ref)
        return ref
