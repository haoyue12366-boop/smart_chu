"""发布清单构造及规范化字节序列。"""

import hashlib
import json

from pydantic import BaseModel

from app.domain.knowledge import ReleaseRef
from app.domain.provenance import ArtifactRef
from app.domain.release_manifest import ReleaseManifest
from app.domain.release_replay import ReleaseReplay
from app.knowledge.snapshot import SchedulingKnowledgeSnapshot


def encode(model: BaseModel) -> bytes:
    return json.dumps(
        model.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def make_manifest(
    snapshot: SchedulingKnowledgeSnapshot,
    artifacts: tuple[ArtifactRef, ...],
    replays: tuple[ReleaseReplay, ...] = (),
) -> ReleaseManifest:
    source = snapshot.knowledge
    return ReleaseManifest(
        release_id=source.release_id,
        knowledge_version=source.knowledge_version,
        rule_version=source.scope.rule_version,
        snapshot_id=snapshot.snapshot_id,
        snapshot_hash=snapshot.content_hash,
        release_kind=source.scope.release_kind,
        artifacts=tuple(sorted(artifacts, key=lambda a: a.path)),
        replays=replays,
    )


def manifest_ref(manifest: ReleaseManifest) -> ReleaseRef:
    return ReleaseRef(
        release_id=manifest.release_id,
        knowledge_version=manifest.knowledge_version,
        rule_version=manifest.rule_version,
        snapshot_id=manifest.snapshot_id,
        release_kind=manifest.release_kind,
        manifest_hash=hashlib.sha256(encode(manifest)).hexdigest(),
    )
