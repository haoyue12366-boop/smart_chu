"""确定性的图谱投影定义；只有真实依赖生成 PRECEDES。"""

import hashlib
import json
from collections import Counter

from pydantic import BaseModel

from app.domain.base import Digest, FrozenModel, NonEmpty, NonNegativeInt, content_hash
from app.domain.knowledge_release import ReviewedRelease

NODE_KINDS = frozenset(
    {
        "KnowledgeRelease",
        "RecipeVersion",
        "OperationTemplate",
        "MaterialSpec",
        "Ingredient",
        "DeviceProfile",
        "DeviceInstance",
        "DeviceType",
        "ProcessingRule",
        "Evidence",
        "RecipeContext",
        "Reservation",
        "FixedProgram",
    }
)
EDGE_KINDS = frozenset(
    {
        "HAS_RECIPE",
        "HAS_OPERATION",
        "HAS_MATERIAL",
        "PRECEDES",
        "CONSUMES",
        "PRODUCES",
        "BASED_ON",
        "REQUIRES_PROFILE",
        "SUPPORTS_PROFILE",
        "GOVERNED_BY",
        "SUPPORTED_BY",
        "HAS_PROFILE",
        "HAS_DEVICE",
        "HAS_RULE",
        "HAS_EVIDENCE",
        "HAS_DEVICE_TYPE",
        "HAS_CONTEXT",
        "HAS_RESERVATION",
        "HAS_PROGRAM",
        "HAS_MEMBER",
    }
)
SCHEMA_QUERIES = (
    "CREATE CONSTRAINT knowledge_entity_key IF NOT EXISTS "
    "FOR (n:KnowledgeEntity) REQUIRE n.key IS UNIQUE",
    "CREATE INDEX knowledge_release_lookup IF NOT EXISTS "
    "FOR (n:KnowledgeEntity) ON (n.release_id, n.knowledge_version)",
    "CREATE INDEX knowledge_recipe_lookup IF NOT EXISTS "
    "FOR (n:RecipeVersion) ON (n.release_id, n.recipe_id)",
)


class GraphNode(FrozenModel):
    key: Digest
    kind: NonEmpty
    release_id: NonEmpty
    knowledge_version: NonEmpty
    payload_json: str
    ordinal: NonNegativeInt = 0
    recipe_id: str = ""
    name: str = ""
    bound_hash: Digest | None = None


class GraphEdge(FrozenModel):
    key: Digest
    kind: NonEmpty
    start_key: Digest
    end_key: Digest
    payload_json: str


class GraphPlan(FrozenModel):
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]


class GraphProjectionReport(FrozenModel):
    release_id: NonEmpty
    knowledge_version: NonEmpty
    release_hash: Digest
    graph_hash: Digest
    node_counts: dict[str, int]
    edge_counts: dict[str, int]


def _json(value: object) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def entity_key(release_id: str, kind: str, *parts: str) -> str:
    return hashlib.sha256(_json((release_id, kind, *parts)).encode()).hexdigest()


def projection_plan(release: ReviewedRelease) -> GraphPlan:
    nodes: dict[str, GraphNode] = {}
    edges: dict[str, GraphEdge] = {}

    def node(
        kind: str,
        identity: tuple[str, ...],
        payload: object,
        *,
        ordinal: int = 0,
        recipe_id: str = "",
        name: str = "",
        bound_hash: str | None = None,
    ) -> str:
        if kind not in NODE_KINDS:
            raise ValueError("未知图谱节点类别")
        key = entity_key(release.release_id, kind, *identity)
        value = GraphNode(
            key=key,
            kind=kind,
            release_id=release.release_id,
            knowledge_version=release.knowledge_version,
            payload_json=_json(payload),
            ordinal=ordinal,
            recipe_id=recipe_id,
            name=name,
            bound_hash=bound_hash,
        )
        if key in nodes and nodes[key] != value:
            raise ValueError("图谱实体稳定身份冲突")
        nodes[key] = value
        return key

    def edge(
        kind: str, source: str, target: str, payload: object = None, identity: str = ""
    ) -> None:
        if kind not in EDGE_KINDS:
            raise ValueError("未知图谱关系类别")
        key = entity_key(release.release_id, kind, source, target, identity)
        value = GraphEdge(
            key=key, kind=kind, start_key=source, end_key=target, payload_json=_json(payload)
        )
        if key in edges and edges[key] != value:
            raise ValueError("图谱关系稳定身份冲突")
        edges[key] = value

    metadata = release.model_dump(mode="json", exclude={"recipes", "profiles", "provenance"})
    root = node("KnowledgeRelease", (), metadata, bound_hash=release.content_hash)
    evidences = {}
    for index, record in enumerate(release.provenance):
        key = node("Evidence", (record.provenance_id,), record, ordinal=index)
        evidences[record.provenance_id] = key
        edge("HAS_EVIDENCE", root, key)

    def supported(key: str, refs: tuple[str, ...]) -> None:
        for ref in dict.fromkeys(refs):
            if ref not in evidences:
                raise ValueError("图谱来源引用不存在")
            edge("SUPPORTED_BY", key, evidences[ref])

    profiles = {}
    for index, profile in enumerate(release.profiles):
        key = node(
            "DeviceProfile", (profile.profile_id,), profile, ordinal=index, name=profile.mode
        )
        profiles[profile.profile_id] = key
        category = node("DeviceType", (profile.device_type,), {"device_type": profile.device_type})
        edge("HAS_DEVICE_TYPE", root, category)
        edge("HAS_PROFILE", category, key)
        supported(key, profile.provenance_refs)
    for device in release.scope.devices:
        key = node("DeviceInstance", (device.device_instance_id,), device)
        edge("HAS_DEVICE", root, key)
        for profile_id in device.capability_refs:
            edge("SUPPORTS_PROFILE", key, profiles[profile_id])
        supported(key, device.evidence_refs)
    rules = {}
    for rule in release.rules:
        key = node("ProcessingRule", (rule.rule_id,), rule)
        rules[rule.rule_id] = key
        edge("HAS_RULE", root, key)
        supported(key, rule.evidence_refs)
    recipe_keys = {}
    operation_keys = {}
    choices = {choice.choice_id: choice.device_ids for choice in release.scope.device_choices}
    for index, recipe in enumerate(release.recipes):
        rid = recipe.recipe_id.root
        key = node("RecipeVersion", (rid,), recipe, ordinal=index, recipe_id=rid, name=recipe.name)
        recipe_keys[rid] = key
        edge("HAS_RECIPE", root, key)
        supported(key, recipe.provenance_refs)
        specs = {}
        for spec in recipe.material_specs:
            spec_key = node(
                "MaterialSpec", (rid, spec.spec_id), spec, recipe_id=rid, name=spec.name
            )
            specs[spec.spec_id] = spec_key
            edge("HAS_MATERIAL", key, spec_key)
            ingredient = node(
                "Ingredient", (spec.ingredient_id,), {"ingredient_id": spec.ingredient_id}
            )
            edge("BASED_ON", spec_key, ingredient)
            supported(spec_key, spec.provenance_refs)
        ops = {}
        for op in recipe.operations:
            op_key = node("OperationTemplate", (rid, op.operation_id.root), op, recipe_id=rid)
            ops[op.operation_id] = op_key
            operation_keys[(rid, op.operation_id)] = op_key
            edge("HAS_OPERATION", key, op_key)
            supported(op_key, op.provenance_refs)
            for relation, demands in (
                ("CONSUMES", op.material_inputs),
                ("PRODUCES", op.material_outputs),
            ):
                for number, demand in enumerate(demands):
                    edge(relation, op_key, specs[demand.spec_id], demand, str(number))
            for rule_id in op.execution_policy.shared_prep_rule_ids:
                edge("GOVERNED_BY", op_key, rules[rule_id])
            for use in op.resource_requirements:
                if use.resource_type != "DEVICE":
                    continue
                ids = choices.get(use.resource_id, (use.resource_id,))
                candidates = {
                    p
                    for d in release.scope.devices
                    if d.device_instance_id in ids
                    for p in d.capability_refs
                }
                mode = next((v.value for v in use.configuration if v.parameter == "mode"), None)
                for profile in release.profiles:
                    if profile.profile_id in candidates and (mode is None or profile.mode == mode):
                        if not use.profile_options or profile.profile_id in use.profile_options:
                            edge("REQUIRES_PROFILE", op_key, profiles[profile.profile_id])
        for number, dependency in enumerate(recipe.dependencies):
            edge(
                "PRECEDES",
                ops[dependency.predecessor_id],
                ops[dependency.successor_id],
                dependency,
                str(number),
            )
    for context in release.scope.recipe_contexts:
        rid = context.recipe_id.root
        key = node("RecipeContext", (rid,), context, recipe_id=rid)
        edge("HAS_CONTEXT", recipe_keys[rid], key)
        for reservation in context.resource_reservations:
            reservation_key = node(
                "Reservation", (rid, reservation.reservation_id), reservation, recipe_id=rid
            )
            edge("HAS_RESERVATION", key, reservation_key)
            for member in reservation.members:
                edge("HAS_MEMBER", reservation_key, operation_keys[(rid, member)])
        for program in context.program_constraints:
            program_key = node("FixedProgram", (rid, program.program_id), program, recipe_id=rid)
            edge("HAS_PROGRAM", key, program_key)
            for member in (
                *program.before_group,
                *program.intervention_group,
                *program.after_group,
            ):
                edge("HAS_MEMBER", program_key, operation_keys[(rid, member)])
    if any(e.start_key not in nodes or e.end_key not in nodes for e in edges.values()):
        raise ValueError("图谱关系存在悬空端点")
    return GraphPlan(
        nodes=tuple(nodes[key] for key in sorted(nodes)),
        edges=tuple(edges[key] for key in sorted(edges)),
    )


def projection_report(release: ReviewedRelease, plan: GraphPlan) -> GraphProjectionReport:
    return GraphProjectionReport(
        release_id=release.release_id,
        knowledge_version=release.knowledge_version,
        release_hash=release.content_hash,
        graph_hash=content_hash(plan),
        node_counts=dict(Counter(n.kind for n in plan.nodes)),
        edge_counts=dict(Counter(e.kind for e in plan.edges)),
    )
