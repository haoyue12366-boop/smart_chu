"""通过注入的真实 Neo4j Driver 原子重建版本；在线仓储不依赖本模块。"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from pydantic import JsonValue, TypeAdapter

from app.domain.knowledge_release import ReviewedRelease
from app.knowledge.graph_schema import (
    SCHEMA_QUERIES,
    GraphEdge,
    GraphNode,
    GraphPlan,
    GraphProjectionReport,
    entity_key,
    projection_plan,
    projection_report,
)

JSON_OBJECT = TypeAdapter(dict[str, JsonValue])

if TYPE_CHECKING:
    from neo4j import Driver, ManagedTransaction


class GraphProjector:
    def __init__(self, driver: Driver, database: str = "neo4j") -> None:
        self.driver = driver
        self.database = database

    def project(self, release: ReviewedRelease) -> GraphProjectionReport:
        release = ReviewedRelease.model_validate(release)
        plan = projection_plan(release)
        for query in SCHEMA_QUERIES:
            self.driver.execute_query(query, database_=self.database)
        with self.driver.session(database=self.database) as session:
            session.execute_write(self._write, release, plan)
        return projection_report(release, plan)

    @staticmethod
    def _write(tx: ManagedTransaction, release: ReviewedRelease, plan: GraphPlan) -> None:
        root_key = entity_key(release.release_id, "KnowledgeRelease")
        record = tx.run(
            "MERGE (r:KnowledgeEntity:KnowledgeRelease {key:$key}) "
            "SET r._projection_lock = coalesce(r._projection_lock,0) + 1 "
            "RETURN r.bound_hash AS bound_hash",
            key=root_key,
        ).single(strict=True)
        if record is None:
            raise ValueError("图谱版本锁失败")
        if record["bound_hash"] not in (None, release.content_hash):
            raise ValueError("不可变图谱发布不能被不同内容覆盖")
        tx.run(
            "MATCH (n:KnowledgeEntity {release_id:$release_id}) WHERE n.key <> $root_key "
            "DETACH DELETE n",
            release_id=release.release_id,
            root_key=root_key,
        ).consume()
        tx.run("MATCH (r:KnowledgeEntity {key:$key})-[e]-() DELETE e", key=root_key).consume()
        for kind in sorted({node.kind for node in plan.nodes}):
            rows = [n.model_dump(mode="json") for n in plan.nodes if n.kind == kind]
            # 标签只来自 graph_schema 内部白名单，用户 ID/名称/内容均为参数。
            tx.run(
                f"UNWIND $rows AS row MERGE (n:KnowledgeEntity:{kind} {{key:row.key}}) "
                "SET n += row",
                rows=rows,
            ).consume()
        for kind in sorted({edge.kind for edge in plan.edges}):
            rows = [e.model_dump(mode="json") for e in plan.edges if e.kind == kind]
            tx.run(
                "UNWIND $rows AS row MATCH (a:KnowledgeEntity {key:row.start_key}), "
                "(b:KnowledgeEntity {key:row.end_key}) "
                f"MERGE (a)-[e:{kind} {{key:row.key}}]->(b) SET e += row",
                rows=rows,
            ).consume()
        actual = GraphProjector._rows(tx, release.release_id, release.knowledge_version)
        if actual != plan:
            raise ValueError("图谱导入核对失败；事务回滚")

    @staticmethod
    def _rows(tx: ManagedTransaction, release_id: str, knowledge_version: str) -> GraphPlan:
        nodes = []
        for record in tx.run(
            "MATCH (n {release_id:$release_id, knowledge_version:$version}) "
            "RETURN n{.key,.kind,.release_id,.knowledge_version,.payload_json,.ordinal,"
            ".recipe_id,.name,.bound_hash} AS data, labels(n) AS labels ORDER BY n.key",
            release_id=release_id,
            version=knowledge_version,
        ):
            node = GraphNode.model_validate(record["data"])
            if set(record["labels"]) != {"KnowledgeEntity", node.kind}:
                raise ValueError("图谱节点标签与声明类别不一致")
            nodes.append(node)
        edges = tuple(
            GraphEdge.model_validate(record["data"])
            for record in tx.run(
                "MATCH (n {release_id:$release_id, knowledge_version:$version})-[e]-() "
                "WITH DISTINCT e, startNode(e) AS a, endNode(e) AS b "
                "RETURN {key:e.key,kind:type(e),start_key:a.key,end_key:b.key,"
                "payload_json:e.payload_json} AS data ORDER BY e.key",
                release_id=release_id,
                version=knowledge_version,
            )
        )
        return GraphPlan(nodes=tuple(nodes), edges=edges)

    def read(self, release_id: str, knowledge_version: str) -> ReviewedRelease:
        with self.driver.session(database=self.database) as session:
            return session.execute_read(self._read, release_id, knowledge_version)

    @staticmethod
    def _read(tx: ManagedTransaction, release_id: str, knowledge_version: str) -> ReviewedRelease:
        actual = GraphProjector._rows(tx, release_id, knowledge_version)
        roots = [n for n in actual.nodes if n.kind == "KnowledgeRelease"]
        if len(roots) != 1:
            raise ValueError("图谱发布不存在或版本不一致")
        payload = JSON_OBJECT.validate_python(json.loads(roots[0].payload_json))
        for field, kind in (
            ("recipes", "RecipeVersion"),
            ("profiles", "DeviceProfile"),
            ("provenance", "Evidence"),
        ):
            payload[field] = [
                JSON_OBJECT.validate_python(json.loads(n.payload_json))
                for n in sorted(actual.nodes, key=lambda n: n.ordinal)
                if n.kind == kind
            ]
        release = ReviewedRelease.model_validate(payload)
        if release.content_hash != roots[0].bound_hash or projection_plan(release) != actual:
            raise ValueError("图谱内容、必需关系或绑定哈希不匹配")
        return release
