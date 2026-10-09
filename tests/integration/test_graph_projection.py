"""显式真实 Neo4j 集成；真实待审样本作为 development 投影，不能冒充批准发布。"""

import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from neo4j import GraphDatabase

from app.knowledge.graph_projection import GraphProjector
from app.pipeline.development import load_development_knowledge
from app.pipeline.release_input import prepare_release_input

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.real_neo4j


@pytest.fixture
def graph():
    if os.environ.get("SMART_COOKING_NEO4J_TEST") != "1":
        pytest.skip("需显式 SMART_COOKING_NEO4J_TEST=1 才连接真实 Neo4j")
    values = dict(
        line.split("=", 1)
        for line in (ROOT / ".env").read_text("utf-8").splitlines()
        if line and not line.startswith("#")
    )
    values.update(os.environ)
    with GraphDatabase.driver(
        values["NEO4J_URI"], auth=(values["NEO4J_USERNAME"], values["NEO4J_PASSWORD"])
    ) as driver:
        driver.verify_connectivity()
        prefix = "test-p1-" + uuid4().hex
        yield driver, GraphProjector(driver), prefix
        driver.execute_query(
            "MATCH (n:KnowledgeEntity) WHERE n.release_id STARTS WITH $prefix DETACH DELETE n",
            prefix=prefix,
            database_="neo4j",
        )


def release(release_id, suffix="one"):
    knowledge = load_development_knowledge(ROOT)
    manifest = json.loads((ROOT / "tests/fixtures/fixture_manifest.json").read_text("utf-8"))
    ids = tuple(entry["recipe_id"] for entry in manifest["real_samples"])
    return prepare_release_input(
        knowledge,
        release_id=release_id,
        recipe_ids=ids,
        knowledge_version="test-development-" + suffix,
    )


def test_real_graph_idempotence_and_duplicate_names(graph):
    driver, projector, prefix = graph
    data = release(prefix + "-one")
    first = projector.project(data)
    second = projector.project(data)
    assert first == second
    assert first.node_counts["RecipeVersion"] == 12
    assert first.node_counts["OperationTemplate"] == sum(len(r.operations) for r in data.recipes)
    assert first.edge_counts["PRECEDES"] == sum(len(r.dependencies) for r in data.recipes)
    assert projector.read(data.release_id, data.knowledge_version) == data
    records, _, _ = driver.execute_query(
        "MATCH (r:RecipeVersion {release_id:$release_id}) RETURN r.recipe_id AS id, r.name AS name",
        release_id=data.release_id,
        database_="neo4j",
    )
    prawns = [r for r in records if r["name"] == "麻辣对虾"]
    assert len(prawns) == len({r["id"] for r in prawns}) == 2


def test_versions_are_separate_and_existing_release_cannot_be_overwritten(graph):
    _, projector, prefix = graph
    first, second = release(prefix + "-one"), release(prefix + "-two", "two")
    projector.project(first)
    projector.project(second)
    assert projector.read(first.release_id, first.knowledge_version) == first
    assert projector.read(second.release_id, second.knowledge_version) == second
    conflicting = second.model_copy(update={"release_id": first.release_id})
    with pytest.raises(ValueError, match="不可变"):
        projector.project(conflicting)
    assert projector.read(first.release_id, first.knowledge_version) == first
    with pytest.raises(ValueError):
        projector.read(first.release_id, "wrong-version")


def test_removed_dependency_is_detected_and_atomic_rebuild_restores_it(graph):
    driver, projector, prefix = graph
    data = release(prefix + "-one")
    initial = projector.project(data)
    driver.execute_query(
        "MATCH (a:KnowledgeEntity {release_id:$release_id})-[e:PRECEDES]->() "
        "WITH e LIMIT 1 DELETE e",
        release_id=data.release_id,
        database_="neo4j",
    )
    with pytest.raises(ValueError, match="图谱"):
        projector.read(data.release_id, data.knowledge_version)
    assert projector.project(data) == initial
    assert projector.read(data.release_id, data.knowledge_version) == data


def test_wrong_node_label_and_incoming_cross_version_edge_are_rejected(graph):
    driver, projector, prefix = graph
    data = release(prefix + "-one")
    projector.project(data)
    driver.execute_query(
        "MATCH (n:RecipeVersion {release_id:$rid}) WITH n LIMIT 1 REMOVE n:RecipeVersion",
        rid=data.release_id,
        database_="neo4j",
    )
    with pytest.raises(ValueError, match="图谱"):
        projector.read(data.release_id, data.knowledge_version)
    projector.project(data)
    driver.execute_query(
        "MATCH (n:RecipeVersion {release_id:$rid}) WITH n LIMIT 1 "
        "CREATE (f:KnowledgeEntity {key:$foreign_key, release_id:$foreign_release}) "
        "CREATE (f)-[:PRECEDES {key:$edge_key, payload_json:'null'}]->(n)",
        rid=data.release_id,
        foreign_key="f" * 64,
        edge_key="e" * 64,
        foreign_release=prefix + "-foreign",
        database_="neo4j",
    )
    with pytest.raises(ValueError, match="图谱"):
        projector.read(data.release_id, data.knowledge_version)


def test_failure_after_destructive_steps_rolls_back_entire_transaction(graph, monkeypatch):
    _, projector, prefix = graph
    data = release(prefix + "-one")
    projector.project(data)
    original = GraphProjector._rows

    def interrupt(*args):
        raise RuntimeError("synthetic interruption before commit")

    monkeypatch.setattr(GraphProjector, "_rows", staticmethod(interrupt))
    with pytest.raises(RuntimeError, match="synthetic interruption"):
        projector.project(data)
    monkeypatch.setattr(GraphProjector, "_rows", staticmethod(original))
    assert projector.read(data.release_id, data.knowledge_version) == data
