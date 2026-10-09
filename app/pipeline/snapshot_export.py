"""固定图版本导出，与规范化发布输入逐项比较后返回快照和独立索引。"""

from datetime import UTC, datetime
from typing import Literal

from app.domain.base import FrozenModel
from app.domain.knowledge_release import ReviewedRelease
from app.domain.snapshot_validation import SnapshotValidationReport as SnapshotValidationReport
from app.knowledge.graph_projection import GraphProjector
from app.knowledge.index import KnowledgeIndex, build_index, validate_index
from app.knowledge.snapshot import SchedulingKnowledgeSnapshot


class SnapshotBuildResult(FrozenModel):
    snapshot: SchedulingKnowledgeSnapshot
    index: KnowledgeIndex
    validation: SnapshotValidationReport


class SnapshotExporter:
    def __init__(self, projector: GraphProjector) -> None:
        self.projector = projector

    def export_snapshot(self, source: ReviewedRelease) -> SnapshotBuildResult:
        source = ReviewedRelease.model_validate(source)
        projected = self.projector.read(source.release_id, source.knowledge_version)
        if projected != source:
            raise ValueError("图谱导出与规范化来源不一致")
        return _build_snapshot(projected, "GRAPH")


def export_canonical_snapshot(source: ReviewedRelease) -> SnapshotBuildResult:
    """由已审核规范化输入重建；记录真实来源，不声称进行过实时图谱核对。"""
    from app.validation.knowledge import validate_knowledge

    source = ReviewedRelease.model_validate_json(source.model_dump_json())
    fresh = validate_knowledge(source.recipes, source.profiles, source.rules, source.scope)
    if fresh != source.validation or not fresh.valid:
        raise ValueError("规范化来源未通过独立知识校验")
    return _build_snapshot(source, "CANONICAL")


def _build_snapshot(
    source: ReviewedRelease, source_kind: Literal["GRAPH", "CANONICAL"]
) -> SnapshotBuildResult:
    snapshot = SchedulingKnowledgeSnapshot(
        exporter_version="snapshot-canonical-v1"
        if source_kind == "CANONICAL"
        else "snapshot-export-v1",
        snapshot_id="snapshot-" + source.content_hash,
        content_hash=source.content_hash,
        build_time=datetime.now(UTC),
        knowledge=source,
    )
    index = build_index(source)
    # 从真实序列化内容重新加载，而非仅比较同一个内存对象。
    restored = SchedulingKnowledgeSnapshot.model_validate_json(snapshot.model_dump_json())
    restored_index = KnowledgeIndex.model_validate_json(index.model_dump_json())
    if restored.knowledge != source:
        raise ValueError("快照序列化丢失调度语义")
    validate_index(restored.knowledge, restored_index)
    from app.domain.base import content_hash
    from app.knowledge.graph_schema import projection_plan

    return SnapshotBuildResult(
        snapshot=snapshot,
        index=index,
        validation=SnapshotValidationReport(
            valid=True,
            release_id=source.release_id,
            canonical_hash=source.content_hash,
            snapshot_hash=snapshot.content_hash,
            graph_hash=content_hash(projection_plan(source)),
            source_kind=source_kind,
        ),
    )
