"""真实 Neo4j 导出，故意破坏图关系和规范化来源必须被拒绝。"""

import pytest

from app.pipeline.snapshot_export import SnapshotExporter
from tests.integration.test_graph_projection import release

pytestmark = pytest.mark.real_neo4j


def test_snapshot_export_roundtrip_and_repeatability(graph):
    _, projector, prefix = graph
    source = release(prefix + "-snapshot")
    projector.project(source)
    exporter = SnapshotExporter(projector)
    first = exporter.export_snapshot(source)
    second = exporter.export_snapshot(source)
    assert first.snapshot.content_hash == second.snapshot.content_hash
    assert first.index == second.index
    assert first.snapshot.knowledge == source
    assert first.snapshot.snapshot_id == second.snapshot.snapshot_id
    assert first.validation.valid
    assert len(first.index.recipes) == 12


@pytest.mark.parametrize("kind", ["HAS_MEMBER", "PRECEDES", "SUPPORTED_BY"])
def test_snapshot_rejects_lost_process_or_evidence_relationship(graph, kind):
    driver, projector, prefix = graph
    source = release(prefix + "-snapshot")
    projector.project(source)
    rows, _, _ = driver.execute_query(
        f"MATCH (n:KnowledgeEntity {{release_id:$rid}})-[e:{kind}]->() "
        "WITH e LIMIT 1 DELETE e RETURN 1 AS removed",
        rid=source.release_id,
        database_="neo4j",
    )
    assert len(rows) == 1
    with pytest.raises(ValueError):
        SnapshotExporter(projector).export_snapshot(source)


def test_snapshot_rejects_wrong_canonical_version(graph):
    _, projector, prefix = graph
    source = release(prefix + "-snapshot")
    projector.project(source)
    with pytest.raises(ValueError):
        SnapshotExporter(projector).export_snapshot(
            source.model_copy(update={"knowledge_version": "wrong"})
        )
