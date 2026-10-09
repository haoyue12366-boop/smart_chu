"""只索引静态关系；规格桶不是已选择的共享候选。"""

from collections import defaultdict
from typing import Literal

from app.domain.base import Digest, FrozenModel, NonEmpty, content_hash
from app.domain.canonical_recipe import Dependency
from app.domain.compatibility import GroupRuleSpec
from app.domain.ids import OperationId, RecipeId
from app.domain.knowledge import EvidenceIndexEntry
from app.domain.knowledge_release import ReviewedRelease


class OperationRef(FrozenModel):
    recipe_id: RecipeId
    operation_id: OperationId


class RecipeIndexEntry(FrozenModel):
    recipe_id: RecipeId
    operation_ids: tuple[OperationId, ...]
    material_spec_ids: tuple[NonEmpty, ...]
    dependencies: tuple[Dependency, ...]


class StaticBucket(FrozenModel):
    key: NonEmpty
    members: tuple[OperationRef, ...]


class KnowledgeIndex(FrozenModel):
    schema_version: Literal["1.0"] = "1.0"
    snapshot_hash: Digest
    knowledge_version: NonEmpty
    rule_version: NonEmpty
    recipes: tuple[RecipeIndexEntry, ...]
    material_buckets: tuple[StaticBucket, ...]
    configuration_buckets: tuple[StaticBucket, ...]
    rule_reverse_index: tuple[StaticBucket, ...]
    evidence: tuple[EvidenceIndexEntry, ...]


def build_index(knowledge: ReviewedRelease) -> KnowledgeIndex:
    material: dict[str, list[OperationRef]] = defaultdict(list)
    configuration: dict[str, list[OperationRef]] = defaultdict(list)
    rules: dict[str, list[OperationRef]] = defaultdict(list)
    rows = []
    for recipe in knowledge.recipes:
        specs = {s.spec_id: s for s in recipe.material_specs}
        rows.append(
            RecipeIndexEntry(
                recipe_id=recipe.recipe_id,
                operation_ids=tuple(o.operation_id for o in recipe.operations),
                material_spec_ids=tuple(specs),
                dependencies=recipe.dependencies,
            )
        )
        for op in recipe.operations:
            ref = OperationRef(recipe_id=recipe.recipe_id, operation_id=op.operation_id)
            for key in dict.fromkeys(content_hash(specs[m.spec_id]) for m in op.material_inputs):
                material[key].append(ref)
            for key in dict.fromkeys(
                content_hash(use)
                for use in op.resource_requirements
                if use.resource_type == "DEVICE"
            ):
                configuration[key].append(ref)
            for key in dict.fromkeys(op.execution_policy.shared_prep_rule_ids):
                rules[key].append(ref)

    # 全组规则的绑定位于规则本身，不要求回写原工序才可反向查询。
    for rule in knowledge.rules:
        if rule.kind not in {"SHARED_PREP", "STRICT_TOGETHER"}:
            continue
        try:
            spec = GroupRuleSpec.model_validate_json(rule.group_compatibility_predicate)
        except ValueError:
            continue
        for binding in spec.bindings:
            ref = OperationRef(recipe_id=binding.recipe_id, operation_id=binding.operation_id)
            if ref not in rules[rule.rule_id]:
                rules[rule.rule_id].append(ref)

    def buckets(values: dict[str, list[OperationRef]]) -> tuple[StaticBucket, ...]:
        return tuple(StaticBucket(key=key, members=tuple(values[key])) for key in sorted(values))

    return KnowledgeIndex(
        snapshot_hash=knowledge.content_hash,
        knowledge_version=knowledge.knowledge_version,
        rule_version=knowledge.scope.rule_version,
        recipes=tuple(rows),
        material_buckets=buckets(material),
        configuration_buckets=buckets(configuration),
        rule_reverse_index=buckets(rules),
        evidence=tuple(
            EvidenceIndexEntry(
                evidence_id=p.provenance_id,
                artifact_path=p.source_file,
                artifact_hash=p.source_hash,
                locator=p.field_path,
            )
            for p in knowledge.provenance
        ),
    )


def validate_index(knowledge: ReviewedRelease, index: KnowledgeIndex) -> None:
    if build_index(knowledge) != index:
        raise ValueError("知识索引的版本、引用或关系与快照不一致")
