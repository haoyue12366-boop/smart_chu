"""加载用户授权的 V3 开发基线；不将它升级为审核发布。"""

import hashlib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from app.domain.base import Digest, FrozenModel
from app.domain.canonical_recipe import CanonicalRecipeModel
from app.domain.knowledge import DeviceChoice, ReleaseScope
from app.domain.provenance import ArtifactRef, ProvenanceRecord
from app.domain.recipe_context import (
    RecipeProgram,
    RecipeReservation,
    RecipeSchedulingContext,
    SourceOperationGroup,
)
from app.domain.resources import ConflictPolicy, DeviceInstance, DeviceProfile
from app.pipeline.device_import import import_devices
from app.pipeline.device_profiles import build_device_profiles
from app.pipeline.import_raw import import_recipes


class _RecipeInput(BaseModel):
    # 此处投影校验所需字段；完整 JSON 由 source_artifacts 绑定，固定程序不会从源中删除。
    canonical: CanonicalRecipeModel
    evidence: tuple[ProvenanceRecord, ...]
    source_groups: dict[str, tuple[str, ...]]
    resource_reservations: tuple[RecipeReservation, ...]
    program_constraints: tuple[RecipeProgram, ...]


class _ResourceInput(BaseModel):
    resource_id: str
    physical_resource_id: str
    component_id: str
    policy: ConflictPolicy


class _DevelopmentInput(BaseModel):
    release_kind: Literal["development"]
    knowledge_version: str
    review_status: Literal["NEEDS_REVIEW"]
    approved_count: Literal[0]
    recipes: tuple[_RecipeInput, ...]
    resources: tuple[_ResourceInput, ...]
    source_hashes: dict[str, Digest]
    provenance: tuple[ProvenanceRecord, ...]
    device_assumptions: tuple[str, ...]


class DevelopmentKnowledge(FrozenModel):
    knowledge_version: str
    recipes: tuple[CanonicalRecipeModel, ...]
    profiles: tuple[DeviceProfile, ...]
    scope: ReleaseScope
    provenance: tuple[ProvenanceRecord, ...]
    source_artifacts: tuple[ArtifactRef, ...]


def load_development_knowledge(
    root: Path,
    relative_path: str = "data/development/development-v3-rebased-v2/dataset.json",
) -> DevelopmentKnowledge:
    path = root / relative_path
    dataset_ref = ArtifactRef(
        path=relative_path,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        media_type="application/json",
    )
    path = dataset_ref.verify(root)
    data = _DevelopmentInput.model_validate_json(path.read_bytes())
    artifacts = {dataset_ref.path: dataset_ref}
    for name, digest in data.source_hashes.items():
        ref = ArtifactRef(path=name, sha256=digest, media_type="application/octet-stream")
        ref.verify(root)
        artifacts[name] = ref
    recipes = tuple(row.canonical for row in data.recipes)
    expected = tuple(row.recipe_id for row in import_recipes(root / "docs/recipes_100.csv"))
    if len(expected) != 100 or tuple(r.recipe_id for r in recipes) != expected:
        raise ValueError("开发基线没有完整保留原始 100 ID")
    if any(r.review_status != "NEEDS_REVIEW" or r.approval is not None for r in recipes):
        raise ValueError("开发来源不能携带人工批准")
    rule_versions = {
        use.rule_version
        for recipe in recipes
        for op in recipe.operations
        for use in op.resource_requirements
        if use.resource_type == "DEVICE"
    }
    if len(rule_versions) != 1:
        raise ValueError("现存开发数据的设备规则版本不一致")
    rule_version = rule_versions.pop()
    raw = import_devices(root / "docs/设备参数清单参考.json")
    profiles = build_device_profiles(raw, rule_version)
    device_ref = ArtifactRef(
        path="docs/设备参数清单参考.json", sha256=raw.source_sha256, media_type="application/json"
    )
    artifacts[device_ref.path] = device_ref
    mapping_id = f"device-mapping:{dataset_ref.sha256}"
    provenance = (
        *data.provenance,
        *(entry for row in data.recipes for entry in row.evidence),
        ProvenanceRecord(
            provenance_id=f"device-source:{raw.source_sha256}",
            origin="SOURCE_DERIVED",
            source_file=device_ref.path,
            source_hash=device_ref.sha256,
            record_id="devices",
            field_path="/设备清单",
            text_span="单位换算和字段映射直接来自原始设备清单；能力仍待审核。",
            artifact_ref=device_ref,
        ),
        ProvenanceRecord(
            provenance_id=mapping_id,
            origin="MODEL_SUGGESTION",
            source_file=dataset_ref.path,
            source_hash=dataset_ref.sha256,
            record_id="devices",
            field_path="/resources",
            text_span="；".join(data.device_assumptions),
            artifact_ref=dataset_ref,
        ),
    )
    for entry in provenance:
        actual = artifacts.get(entry.source_file)
        if actual is None or actual.sha256 != entry.source_hash:
            raise ValueError(f"开发来源无法验证：{entry.provenance_id}")
        if entry.artifact_ref is not None:
            bound = artifacts.get(entry.artifact_ref.path)
            if bound is None or bound.sha256 != entry.artifact_ref.sha256:
                raise ValueError(f"开发内容引用无法验证：{entry.provenance_id}")
    type_map = {
        "burner_1": ("灶具", None),
        "burner_2": ("灶具", None),
        "steam_oven_1": ("蒸箱", None),
        "oven_1": ("烤箱", None),
        "fridge_cold_1": ("冰箱", "冷藏"),
        "fridge_freezer_1": ("冰箱", "冷冻"),
    }
    devices = []
    for resource in data.resources:
        if resource.resource_id == "human_1":
            continue
        if resource.resource_id not in type_map:
            raise ValueError("开发设备没有明确的能力映射")
        device_type, mode = type_map[resource.resource_id]
        capabilities = tuple(
            p.profile_id
            for p in profiles
            if p.device_type == device_type and (mode is None or p.mode == mode)
        )
        devices.append(
            DeviceInstance(
                device_instance_id=resource.resource_id,
                physical_resource_id=resource.physical_resource_id,
                component_id=resource.component_id,
                conflict_policy=resource.policy,
                capability_refs=capabilities,
                rule_version=rule_version,
                evidence_refs=(mapping_id,),
                review_status="NEEDS_REVIEW",
            )
        )
    scope = ReleaseScope(
        release_kind="development",
        rule_version=rule_version,
        time_grid_sec=1,
        devices=tuple(devices),
        device_choices=(
            DeviceChoice(choice_id="stove_choice", device_ids=("burner_1", "burner_2")),
        ),
        evidence_ids=tuple(p.provenance_id for p in provenance),
        expected_recipe_ids=expected,
        required_recipes=recipes,
        recipe_contexts=tuple(
            RecipeSchedulingContext(
                recipe_id=row.canonical.recipe_id,
                source_groups=tuple(
                    SourceOperationGroup(source_step=step, operation_ids=ids)
                    for step, ids in row.source_groups.items()
                ),
                resource_reservations=row.resource_reservations,
                program_constraints=row.program_constraints,
            )
            for row in data.recipes
        ),
    )
    return DevelopmentKnowledge(
        knowledge_version=data.knowledge_version,
        recipes=recipes,
        profiles=profiles,
        scope=scope,
        provenance=provenance,
        source_artifacts=tuple(artifacts.values()),
    )
