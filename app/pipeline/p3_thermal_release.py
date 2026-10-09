"""新建P3热边界开发版本；原准备包、工艺时长与人工审核性质保持不变。"""

import hashlib
import json
from pathlib import Path
from typing import Literal

from app.domain.base import Digest, FrozenModel, NonEmpty, content_hash
from app.domain.canonical_recipe import CanonicalRecipeModel
from app.domain.compatibility import GroupRuleSpec
from app.domain.knowledge_release import ReviewedRelease
from app.domain.provenance import ArtifactRef, ProvenanceRecord
from app.knowledge.loader import load_release, read_release_ref
from app.validation.knowledge import validate_knowledge

VERSION = "development-v3-p3-thermal-v1"
RULE_VERSION = "development-shared-v2"


class ThermalAmendment(FrozenModel):
    amendment_id: Literal["p3-thermal-boundary-v1"]
    base_release_id: NonEmpty
    base_content_hash: Digest
    authorization_ref: NonEmpty
    authority: Literal["DELEGATED_DEVELOPMENT_ESTIMATE"]
    review_status: Literal["NEEDS_REVIEW"]
    formal_human_review_complete: Literal[False]
    measured: Literal[False]
    rule_id: Literal["P3-H02-delegated-v1"]
    thermal_model: Literal["COLD_LOAD_SETUP_PREHEAT_HEAT_STOP_UNLOAD"]
    scope_note: NonEmpty
    logical_port_note: NonEmpty
    source_heat_sec: Literal[720]
    source_operation_count: Literal[10]
    outer_duration_sec: Literal[1500]
    stage_durations_sec: dict[str, int]
    cross_batch_reuse_enabled: Literal[False]
    unreviewed_temperature_conversion_enabled: Literal[False]


def _artifact(root: Path, path: Path) -> ArtifactRef:
    path = path.resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("增补来源必须位于项目归档范围")
    return ArtifactRef(
        path=path.relative_to(root.resolve()).as_posix(),
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        media_type="application/json" if path.suffix == ".json" else "text/x-python",
    )


def _reversion(recipe: CanonicalRecipeModel) -> CanonicalRecipeModel:
    if recipe.approval is not None:
        raise ValueError("技术迁移不能继承人工签名")
    return CanonicalRecipeModel.model_validate(
        recipe.model_copy(
            update={
                "recipe_version": recipe.recipe_version + "+thermal-v1",
                "operations": tuple(
                    o.model_copy(
                        update={
                            "resource_requirements": tuple(
                                u.model_copy(update={"rule_version": RULE_VERSION})
                                for u in o.resource_requirements
                            )
                        }
                    )
                    for o in recipe.operations
                ),
            }
        )
    )


def prepare_thermal_release(
    root: Path, *, amendment_path: Path | None = None
) -> tuple[ReviewedRelease, dict[str, object]]:
    releases = root / "data/preparations/p3-v1/releases"
    baseline = load_release(
        releases, read_release_ref(releases, "development-v3-p3-preparation-v1-all")
    )
    old = baseline.snapshot.knowledge
    path = amendment_path or root / "data/development/p3-thermal-boundary-v1.json"
    amendment = ThermalAmendment.model_validate_json(path.read_bytes())
    if (amendment.base_release_id, amendment.base_content_hash) != (
        old.release_id,
        old.content_hash,
    ):
        raise ValueError("热边界增补未绑定当前准备包")
    if amendment.stage_durations_sec != {
        "LOAD": 120,
        "PREPARE": 120,
        "PREHEAT": 300,
        "HEAT": 720,
        "UNLOAD": 240,
    }:
        raise ValueError("增补时长与完整源动作不一致")
    auth = json.loads(
        (root / "data/development/authorizations/p3-delegation-v1.json").read_text(encoding="utf-8")
    )
    if auth["authorization_id"] != amendment.authorization_ref or auth["source"] != "USER_MESSAGE":
        raise ValueError("热边界增补缺少匹配用户委托")
    source_document = root / "data/preparations/p3-v1/reviewed_release.json"
    if ReviewedRelease.model_validate_json(source_document.read_bytes()) != old:
        raise ValueError("准备包重建输入与实际发布不同")
    artifacts = tuple(
        _artifact(root, p)
        for p in (
            path,
            source_document,
            root / "app/pipeline/p3_thermal_release.py",
        )
    )
    provenance = (
        *old.provenance,
        ProvenanceRecord(
            provenance_id=amendment.amendment_id,
            origin="MODEL_SUGGESTION",
            source_file=artifacts[0].path,
            source_hash=artifacts[0].sha256,
            record_id=amendment.amendment_id,
            field_path="/thermal_model",
            text_span=amendment.scope_note + amendment.logical_port_note,
            artifact_ref=artifacts[0],
        ),
    )
    recipes = tuple(_reversion(r) for r in old.recipes)
    recipe_map = {r.recipe_id: r for r in recipes}
    old_recipes = {r.recipe_id: r for r in old.recipes}
    rules = []
    found = False
    for rule in old.rules:
        spec = GroupRuleSpec.model_validate_json(rule.group_compatibility_predicate)
        bindings = []
        for binding in spec.bindings:
            before = old_recipes[binding.recipe_id]
            before_op = next(o for o in before.operations if o.operation_id == binding.operation_id)
            if (
                binding.recipe_hash != before.semantic_hash()
                or binding.operation_hash != content_hash(before_op)
            ):
                raise ValueError("准备规则引用的源内容已失效")
            recipe = recipe_map[binding.recipe_id]
            op = next(o for o in recipe.operations if o.operation_id == binding.operation_id)
            bindings.append(
                binding.model_copy(
                    update={
                        "recipe_hash": recipe.semantic_hash(),
                        "operation_hash": content_hash(op),
                    }
                )
            )
        spec = spec.model_copy(update={"bindings": tuple(bindings)})
        evidence = rule.evidence_refs
        if rule.rule_id == amendment.rule_id:
            if (
                rule.kind != "STRICT_TOGETHER"
                or rule.review_status != "NEEDS_REVIEW"
                or spec.authorization_ref != amendment.authorization_ref
                or spec.duration_sec != amendment.source_heat_sec
            ):
                raise ValueError("增补规则类型、授权或时长不一致")
            spec = spec.model_copy(
                update={
                    "thermal_model": amendment.thermal_model,
                    "scope_note": amendment.scope_note + amendment.logical_port_note,
                }
            )
            evidence = (*evidence, amendment.amendment_id)
            found = True
        rules.append(
            rule.model_copy(
                update={
                    "rule_version": RULE_VERSION,
                    "group_compatibility_predicate": spec.model_dump_json(),
                    "evidence_refs": evidence,
                }
            )
        )
    if not found:
        raise ValueError("准备版本中缺少目标热规则")
    scope = old.scope.model_copy(
        update={
            "rule_version": RULE_VERSION,
            "devices": tuple(
                d.model_copy(update={"rule_version": RULE_VERSION}) for d in old.scope.devices
            ),
            "required_recipes": tuple(recipe_map[r.recipe_id] for r in old.scope.required_recipes),
            "evidence_ids": tuple(p.provenance_id for p in provenance),
        }
    )
    profiles = tuple(p.model_copy(update={"rule_version": RULE_VERSION}) for p in old.profiles)
    source = ReviewedRelease(
        release_id=VERSION + "-all",
        knowledge_version=VERSION,
        recipes=recipes,
        profiles=profiles,
        rules=tuple(rules),
        scope=scope,
        provenance=provenance,
        source_artifacts=(*old.source_artifacts, *artifacts),
        validation=validate_knowledge(recipes, profiles, tuple(rules), scope),
    )
    bindings_audit = []
    for before, after in zip(old.recipes, recipes, strict=True):
        restored = after.model_copy(
            update={
                "recipe_version": before.recipe_version,
                "operations": tuple(
                    a.model_copy(
                        update={
                            "resource_requirements": tuple(
                                ua.model_copy(update={"rule_version": ub.rule_version})
                                for ub, ua in zip(
                                    b.resource_requirements, a.resource_requirements, strict=True
                                )
                            )
                        }
                    )
                    for b, a in zip(before.operations, after.operations, strict=True)
                ),
            }
        )
        if restored != before:
            raise ValueError("热边界技术迁移改变了源菜谱工艺")
        bindings_audit.append(
            {
                "recipe_id": before.recipe_id.root,
                "before": before.semantic_hash(),
                "after": after.semantic_hash(),
            }
        )
    return source, {
        "base_release_id": old.release_id,
        "base_content_hash": old.content_hash,
        "base_snapshot_hash": baseline.snapshot.content_hash,
        "new_release_id": source.release_id,
        "new_content_hash": source.content_hash,
        "process_changes": 0,
        "recipe_count": len(recipes),
        "operation_count": sum(len(r.operations) for r in recipes),
        "formal_human_review_complete": False,
        "measured": False,
        "amendment_id": amendment.amendment_id,
        "recipe_bindings": bindings_audit,
    }


def stage_thermal_sources(source: ReviewedRelease, root: Path, target: Path) -> None:
    """旧材料取自已验证发布归档；新材料取自工作区，逐项保留原路径和哈希。"""
    releases = root / "data/preparations/p3-v1/releases"
    ref = read_release_ref(releases, "development-v3-p3-preparation-v1-all")
    baseline = load_release(releases, ref)
    old = {a.path: a for a in baseline.snapshot.knowledge.source_artifacts}
    archive = releases / ref.release_id
    for artifact in source.source_artifacts:
        origin = root
        if artifact.path in old:
            if artifact != old[artifact.path]:
                raise ValueError("新版本不能覆盖旧来源身份")
            origin = archive
        payload = artifact.verify(origin).read_bytes()
        destination = target / artifact.path
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            artifact.verify(target)
        else:
            with destination.open("xb") as stream:
                stream.write(payload)
            artifact.verify(target)
