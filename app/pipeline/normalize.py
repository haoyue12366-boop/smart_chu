"""基于明确草稿和拆步依据规范化，不猜测缺失工艺或裁剪冲突参数。"""

from __future__ import annotations

import hashlib
from fractions import Fraction

from app.domain.base import FrozenModel, NonEmpty, ReviewStatus
from app.domain.canonical_recipe import (
    Action,
    CanonicalRecipeModel,
    Dependency,
    ExecutionPolicy,
    OperationTemplate,
)
from app.domain.ids import OperationId
from app.domain.provenance import ProvenanceRecord
from app.domain.resources import ResourceType
from app.pipeline.issues import DataIssue

NORMALIZER_VERSION = "normalize-v1"


class OperationExpansion(FrozenModel):
    source_operation_id: OperationId
    phases: tuple[OperationTemplate, ...]
    evidence_refs: tuple[NonEmpty, ...]


class NormalizationEvidence(FrozenModel):
    normalizer_version: NonEmpty
    output_recipe_version: NonEmpty
    records: tuple[ProvenanceRecord, ...]
    expansions: tuple[OperationExpansion, ...] = ()


class OperationMapping(FrozenModel):
    source_operation_id: OperationId
    normalized_operation_ids: tuple[OperationId, ...]
    evidence_refs: tuple[NonEmpty, ...]


class NormalizationResult(FrozenModel):
    recipe: CanonicalRecipeModel
    issues: tuple[DataIssue, ...]
    provenance: tuple[ProvenanceRecord, ...]
    operation_mappings: tuple[OperationMapping, ...]
    normalizer_version: NonEmpty


def _expanded_operations(
    recipe: CanonicalRecipeModel, evidence: NormalizationEvidence
) -> tuple[tuple[OperationTemplate, ...], tuple[Dependency, ...], tuple[OperationMapping, ...]]:
    expansions = {entry.source_operation_id: entry for entry in evidence.expansions}
    originals = {op.operation_id: op for op in recipe.operations}
    if len(expansions) != len(evidence.expansions) or not expansions.keys() <= originals.keys():
        raise ValueError("拆步来源重复或不存在")
    operations: list[OperationTemplate] = []
    dependencies: list[Dependency] = []
    mappings: list[OperationMapping] = []
    ports: dict[OperationId, tuple[OperationId, OperationId]] = {}
    intervention_targets = {
        i.operation_id for op in recipe.operations for i in op.execution_policy.interventions
    }
    for original in recipe.operations:
        expansion = expansions.get(original.operation_id)
        phases: tuple[OperationTemplate, ...]
        if expansion is None:
            phases, refs = (original,), original.provenance_refs
        else:
            phases, refs = expansion.phases, expansion.evidence_refs
            if not phases or not refs:
                raise ValueError("拆步必须提供明确阶段和依据")
            if (
                original.execution_policy != ExecutionPolicy()
                or original.operation_id in intervention_targets
                or original.execution_policy.fixed_batch_id is not None
                or original.execution_policy.thermal_group_id is not None
            ):
                raise ValueError("介入及固定批次锚点需明确工艺修订，不能由通用规范化拆步改变")
            durations = [phase.duration.execution_sec for phase in phases]
            if any(duration is None for duration in durations):
                raise ValueError("明确拆步仍缺阶段时长")
            total = sum(duration for duration in durations if duration is not None)
            if (
                original.duration.execution_sec is not None
                and total != original.duration.execution_sec
            ):
                raise ValueError("拆步不能悄悄修改原工艺时长")
            if (
                phases[0].material_inputs != original.material_inputs
                or phases[-1].material_outputs != original.material_outputs
                or tuple(loss for phase in phases for loss in phase.losses) != original.losses
            ):
                raise ValueError("拆步不能改变来源的投入、产出或损耗边界")
            if any(phase.review_status == ReviewStatus.APPROVED for phase in phases):
                raise ValueError("规范化不能新增已批准操作")
            if any(phase.required != original.required for phase in phases):
                raise ValueError("拆步不能改变强制工序的必需标记")
            for before, after in zip(phases[:-1], phases[1:], strict=True):
                dependencies.append(
                    Dependency(
                        predecessor_id=before.operation_id,
                        successor_id=after.operation_id,
                        min_lag_sec=0,
                        max_lag_sec=0,
                        reason="按明确阶段拆分复合工序，保持原连续过程",
                        evidence_refs=refs,
                    )
                )
        operations.extend(phases)
        ports[original.operation_id] = (phases[0].operation_id, phases[-1].operation_id)
        mappings.append(
            OperationMapping(
                source_operation_id=original.operation_id,
                normalized_operation_ids=tuple(phase.operation_id for phase in phases),
                evidence_refs=refs,
            )
        )
    for dependency in recipe.dependencies:
        dependencies.append(
            Dependency(
                **{
                    **dependency.model_dump(),
                    "predecessor_id": ports[dependency.predecessor_id][1],
                    "successor_id": ports[dependency.successor_id][0],
                }
            )
        )
    return tuple(operations), tuple(dependencies), tuple(mappings)


def normalize(draft: CanonicalRecipeModel, evidence: NormalizationEvidence) -> NormalizationResult:
    if evidence.normalizer_version != NORMALIZER_VERSION:
        raise ValueError("规范化版本不受支持")
    if draft.review_status not in {ReviewStatus.DRAFT, ReviewStatus.NEEDS_REVIEW}:
        raise ValueError("已审核或退役菜谱需要明确修订，不能重新规范化覆盖")
    if len({record.provenance_id for record in evidence.records}) != len(evidence.records):
        raise ValueError("来源身份重复")
    operations, dependencies, mappings = _expanded_operations(draft, evidence)
    payload = draft.model_dump(mode="json")
    payload["operations"] = [op.model_dump(mode="json") for op in operations]
    payload["dependencies"] = [dep.model_dump(mode="json") for dep in dependencies]
    payload["recipe_version"] = evidence.output_recipe_version
    payload["review_status"] = "NEEDS_REVIEW"

    def quantities(value: object) -> None:
        if isinstance(value, dict):
            if value.get("unit") in {"kg", "L"} and "value" in value and "scale" in value:
                amount = Fraction(value["value"] * 1000, value["scale"])
                value.update(
                    value=amount.numerator,
                    scale=amount.denominator,
                    unit="g" if value["unit"] == "kg" else "ml",
                )
            for child in value.values():
                quantities(child)
        elif isinstance(value, list):
            for child in value:
                quantities(child)

    quantities(payload)
    normalized = CanonicalRecipeModel.model_validate(payload)
    issues: list[DataIssue] = []
    known_refs = {record.provenance_id for record in evidence.records}

    def issue(code: str, path: str, description: str, refs: tuple[str, ...]) -> None:
        identity = hashlib.sha256(f"{draft.recipe_id.root}:{code}:{path}".encode()).hexdigest()[:20]
        issues.append(
            DataIssue(
                issue_id=f"normalize-{identity}",
                recipe_id=draft.recipe_id,
                field_path=path,
                code=code,
                description=description,
                evidence_refs=refs,
                required_action="补充可追溯依据或明确审核补丁后重新验证",
            )
        )

    for index, operation in enumerate(normalized.operations):
        path = f"/operations/{index}"
        if operation.required and operation.duration.execution_sec is None:
            issue(
                "MISSING_DURATION",
                path + "/duration",
                "必需工序缺少选用时长",
                operation.provenance_refs,
            )
        if operation.action == Action.UNKNOWN:
            issue("UNKNOWN_ACTION", path + "/action", "动作仍未明确", operation.provenance_refs)
        if operation.action in {Action.WAIT, Action.CHILL, Action.FREEZE, Action.MARINATE} and any(
            resource.resource_type == ResourceType.HUMAN
            for resource in operation.resource_requirements
        ):
            issue(
                "ACTIVE_PASSIVE_NOT_SEPARATED",
                path,
                "等待与主动人工需明确阶段",
                operation.provenance_refs,
            )
        refs = (
            *operation.provenance_refs,
            *((operation.duration.source_ref,) if operation.duration.source_ref else ()),
        )
        if not refs or not set(refs) <= known_refs:
            issue("MISSING_PROVENANCE", path, "工艺来源引用缺失", operation.provenance_refs)
    for mapping in mappings:
        if not set(mapping.evidence_refs) <= known_refs:
            issue(
                "MISSING_PROVENANCE",
                f"/mapping/{mapping.source_operation_id.root}",
                "拆步缺少来源记录",
                mapping.evidence_refs,
            )
    return NormalizationResult(
        recipe=normalized,
        issues=tuple(issues),
        provenance=evidence.records,
        operation_mappings=mappings,
        normalizer_version=evidence.normalizer_version,
    )
