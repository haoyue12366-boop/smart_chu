"""从指定不可变发布读取，拒绝损坏、混版和隐式回退；无数据库依赖。"""

import hashlib
from pathlib import Path

from pydantic import TypeAdapter

from app.domain.base import FrozenModel
from app.domain.knowledge import ReleaseRef
from app.domain.provenance import ArtifactRef
from app.domain.release_feasibility import ReleasePlanProof, validate_plan_bindings
from app.domain.release_manifest import ReleaseManifest
from app.domain.snapshot_validation import SnapshotValidationReport
from app.knowledge.index import KnowledgeIndex, validate_index
from app.knowledge.snapshot import SchedulingKnowledgeSnapshot


class LoadedRelease(FrozenModel):
    manifest: ReleaseManifest
    snapshot: SchedulingKnowledgeSnapshot
    index: KnowledgeIndex


def release_directory(root: Path, release_id: str) -> Path:
    # 发布 ID 是一个目录名，不接受路径或点目录。
    if release_id in {".", ".."} or any(c in release_id for c in "/\\:"):
        raise ValueError("发布身份不是合法目录名")
    ref = ArtifactRef(
        path=f"{release_id}/manifest.json", sha256="0" * 64, media_type="application/json"
    )
    return root / Path(ref.path).parent


def load_release(root: Path, ref: ReleaseRef) -> LoadedRelease:
    directory = release_directory(root, ref.release_id)
    manifest_ref = ArtifactRef(
        path=f"{ref.release_id}/manifest.json",
        sha256=ref.manifest_hash,
        media_type="application/json",
    )
    manifest = ReleaseManifest.model_validate_json(manifest_ref.verify(root).read_bytes())
    for field in ("release_id", "knowledge_version", "rule_version", "snapshot_id", "release_kind"):
        if getattr(manifest, field) != getattr(ref, field):
            raise ValueError("请求发布身份与 manifest 不一致")
    artifacts = {a.path: a for a in manifest.artifacts}
    if (
        len(artifacts) != len(manifest.artifacts)
        or not {"snapshot.json", "index.json", "snapshot_validation_report.json"}
        <= artifacts.keys()
    ):
        raise ValueError("发布产物缺失或路径重复")
    for artifact in artifacts.values():
        artifact.verify(directory)
    snapshot = SchedulingKnowledgeSnapshot.model_validate_json(
        (directory / "snapshot.json").read_bytes()
    )
    index = KnowledgeIndex.model_validate_json((directory / "index.json").read_bytes())
    knowledge = snapshot.knowledge
    proof = SnapshotValidationReport.model_validate_json(
        (directory / "snapshot_validation_report.json").read_bytes()
    )
    if (
        not proof.valid
        or proof.release_id != knowledge.release_id
        or proof.canonical_hash != knowledge.content_hash
        or proof.snapshot_hash != snapshot.content_hash
    ):
        raise ValueError("快照校验证明与内容不一致或不通过")
    if (
        snapshot.snapshot_id != manifest.snapshot_id
        or snapshot.content_hash != manifest.snapshot_hash
        or knowledge.release_id != manifest.release_id
        or knowledge.knowledge_version != manifest.knowledge_version
        or knowledge.scope.rule_version != manifest.rule_version
        or knowledge.scope.release_kind != manifest.release_kind
    ):
        raise ValueError("快照与 manifest 版本不一致")
    validate_index(knowledge, index)
    if manifest.release_kind == "competition":
        if "single_recipe_plans.json" not in artifacts or len(knowledge.recipes) != 100:
            raise ValueError("正式发布缺少100道单菜计划证明")
        proofs = TypeAdapter(tuple[ReleasePlanProof, ...]).validate_json(
            (directory / "single_recipe_plans.json").read_bytes()
        )
        validate_plan_bindings(
            proofs,
            recipe_ids=tuple(r.recipe_id for r in knowledge.recipes),
            knowledge_version=knowledge.knowledge_version,
            rule_version=knowledge.scope.rule_version,
            snapshot_id=snapshot.snapshot_id,
        )
    for source in knowledge.source_artifacts:
        if artifacts.get(source.path) != source:
            raise ValueError("发布缺失必需来源归档")
    sources = {s.path: s.sha256 for s in knowledge.source_artifacts}
    required_runs = {p.extraction_run_id for p in knowledge.provenance if p.extraction_run_id}
    if required_runs != {r.run_id for r in manifest.replays}:
        raise ValueError("发布缺失必需抽取重放绑定")
    for binding in manifest.replays:
        packaged = binding.run_manifest.model_copy(
            update={"path": "extraction/" + binding.run_manifest.path}
        )
        if artifacts.get(packaged.path) != packaged:
            raise ValueError("发布缺失抽取原始归档清单")
    for evidence in knowledge.provenance:
        if sources.get(evidence.source_file) != evidence.source_hash:
            raise ValueError("证据引用无法追溯到发布来源")
        if (
            evidence.artifact_ref
            and sources.get(evidence.artifact_ref.path) != evidence.artifact_ref.sha256
        ):
            raise ValueError("证据内容引用与归档不一致")
    # 三项分别来自完整、严格的 JSON 解码；上面的内容哈希、版本、索引、
    # 来源及计划绑定也已全部核对。内部读取信封只连接这些不可变引用，
    # 避免再复制整个100菜快照；LoadedRelease 的普通构造/JSON契约仍严格。
    return LoadedRelease.model_construct(manifest=manifest, snapshot=snapshot, index=index)


def read_release_ref(root: Path, release_id: str) -> ReleaseRef:
    path = release_directory(root, release_id) / "manifest.json"
    payload = path.read_bytes()
    manifest = ReleaseManifest.model_validate_json(payload)
    return ReleaseRef(
        release_id=manifest.release_id,
        knowledge_version=manifest.knowledge_version,
        rule_version=manifest.rule_version,
        snapshot_id=manifest.snapshot_id,
        manifest_hash=hashlib.sha256(payload).hexdigest(),
        release_kind=manifest.release_kind,
    )
