"""由明确用户确认发布三层设备版本，保留原工艺与不可变来源。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from app.config import ROOT
from app.domain.base import ReviewStatus, content_hash
from app.domain.canonical_recipe import CanonicalRecipeModel, ReviewStamp
from app.domain.compatibility import GroupRuleSpec
from app.domain.knowledge_release import ReviewedRelease
from app.domain.processing_rules import ProcessingRule
from app.domain.provenance import ArtifactRef, ProvenanceRecord
from app.domain.recipe_context import RecipeSchedulingContext
from app.domain.resources import ConflictPolicy, ResourceUse
from app.knowledge.loader import LoadedRelease, load_release, read_release_ref
from app.pipeline.publish import ReleaseBundle, publish_release
from app.pipeline.snapshot_export import export_canonical_snapshot
from app.validation.knowledge import validate_knowledge

BASE_ID = "delegated-v3-cook-finish-v1-all"
VERSION = "delegated-v3-layered-devices-v1"
RELEASE_ID = VERSION + "-all"
OUTPUT = Path("data/preparations/layered-devices-v1")
RELEASE_ROOT = Path("data/preparations/p4-v1/releases")
AUTH_PATH = OUTPUT / "authorizations/user-confirmation.json"
REVIEW_PATH = OUTPUT / "review.json"
POLICY_PATH = Path("data/policies/p6-cook-layered-v1.json")
REVIEW_ID = "DELEGATED_AGENT:2026-10-08:layered-devices-v1"
DEVICES = frozenset({"steam_oven_1", "oven_1"})


def write_immutable(path: Path, payload: bytes) -> None:
    """相同版本可重建；已有不同内容必须另取版本，不覆盖。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise ValueError("不能覆盖不同的版本内容：" + str(path))
        return
    with path.open("xb") as stream:
        stream.write(payload)


def encode(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def artifact(root: Path, relative: Path) -> ArtifactRef:
    return ArtifactRef(
        path=relative.as_posix(),
        sha256=hashlib.sha256((root / relative).read_bytes()).hexdigest(),
        media_type="text/x-python" if relative.suffix == ".py" else "application/json",
    )


def thermal_state(use: ResourceUse) -> tuple[tuple[str, int | str], ...] | None:
    values = {v.parameter: v.value for v in use.configuration}
    if not {"temperature_c", "mode"} <= values.keys():
        return None
    if type(values["temperature_c"]) is not int or not isinstance(values["mode"], str):
        return None
    return tuple(sorted((p, v) for p, v in values.items() if p != "duration_sec"))


def reservation_decisions(
    source: ReviewedRelease,
) -> tuple[dict[tuple[str, str], bool], list[dict[str, object]]]:
    decisions: dict[tuple[str, str], bool] = {}
    rows: list[dict[str, object]] = []
    recipes = {r.recipe_id: r for r in source.recipes}
    for context in source.scope.recipe_contexts:
        recipe = recipes[context.recipe_id]
        for reservation in context.resource_reservations:
            if not DEVICES.intersection(reservation.resource_options):
                continue
            uses = [
                (operation, use)
                for operation in recipe.operations
                if operation.operation_id in reservation.members
                for use in operation.resource_requirements
                if use.resource_id in reservation.resource_options and use.resource_id in DEVICES
            ]
            states = [thermal_state(use) for _, use in uses]
            compatible = bool(states) and None not in states and len(set(states)) == 1
            compatible = compatible and len(reservation.resource_options) == 1
            decisions[(recipe.recipe_id.root, reservation.reservation_id)] = compatible
            rows.append(
                {
                    "recipe_id": recipe.recipe_id.root,
                    "name": recipe.name,
                    "reservation_id": reservation.reservation_id,
                    "resource_options": list(reservation.resource_options),
                    "policy_before": reservation.policy.value,
                    "policy_after": "STATE_COMPATIBLE" if compatible else "UNARY",
                    "decision": "CONFIRMED_LAYER_REUSE" if compatible else "RETAIN_EXCLUSIVE",
                    "reason": "所有阶段温度、模式、湿度配置明确且一致，可独立使用层位。"
                    if compatible
                    else "阶段配置变化或不明确，保留原整腔独占，不推断新工艺。",
                    "member_device_uses": [
                        {
                            "operation_id": operation.operation_id.root,
                            "action": operation.action.value,
                            "duration_sec": operation.duration.execution_sec,
                            "resource_id": use.resource_id,
                            "configuration": [v.model_dump(mode="json") for v in use.configuration],
                        }
                        for operation, use in uses
                    ],
                }
            )
    return decisions, rows


def update_recipes(
    source: ReviewedRelease,
    decisions: dict[tuple[str, str], bool],
    auth_id: str,
    reviewer: str,
    reviewed_at: datetime,
) -> tuple[CanonicalRecipeModel, ...]:
    contexts = {c.recipe_id: c for c in source.scope.recipe_contexts}
    recipes = []
    for recipe in source.recipes:
        operations = []
        context = contexts.get(recipe.recipe_id)
        for operation in recipe.operations:
            uses = []
            for use in operation.resource_requirements:
                eligible = use.resource_id in DEVICES and thermal_state(use) is not None
                reservations = (
                    [
                        r
                        for r in context.resource_reservations
                        if operation.operation_id in r.members
                        and use.resource_id in r.resource_options
                    ]
                    if context
                    else []
                )
                if reservations:
                    eligible = eligible and all(
                        decisions.get((recipe.recipe_id.root, r.reservation_id), False)
                        for r in reservations
                    )
                if eligible:
                    use = use.model_copy(
                        update={
                            "conflict_policy": ConflictPolicy.STATE_COMPATIBLE,
                            "evidence_refs": (*use.evidence_refs, auth_id, REVIEW_ID),
                        }
                    )
                uses.append(use)
            operations.append(operation.model_copy(update={"resource_requirements": tuple(uses)}))
        if tuple(operations) == recipe.operations:
            recipes.append(recipe)
            continue
        changed = recipe.model_copy(
            update={
                "recipe_version": recipe.recipe_version + "+layered-devices-v1",
                "operations": tuple(operations),
                "approval": None,
                "review_status": ReviewStatus.NEEDS_REVIEW,
            }
        )
        review_id = REVIEW_ID + ":" + recipe.recipe_id.root
        approved = changed.model_copy(
            update={
                "review_status": ReviewStatus.APPROVED,
                "review_patch_refs": (*recipe.review_patch_refs, review_id),
                "approval": ReviewStamp(
                    review_id=review_id,
                    reviewer=reviewer,
                    reviewed_at=reviewed_at,
                    evidence_refs=(auth_id, REVIEW_ID),
                    approved_content_hash=changed.semantic_hash(),
                ),
            }
        )
        recipes.append(CanonicalRecipeModel.model_validate_json(approved.model_dump_json()))
    return tuple(recipes)


def update_rules(
    source: ReviewedRelease, recipes: tuple[CanonicalRecipeModel, ...]
) -> tuple[ProcessingRule, ...]:
    before = {r.recipe_id: r for r in source.recipes}
    after = {r.recipe_id: r for r in recipes}
    rules = []
    for rule in source.rules:
        spec = GroupRuleSpec.model_validate_json(rule.group_compatibility_predicate)
        bindings = []
        for binding in spec.bindings:
            old = before[binding.recipe_id]
            new = after[binding.recipe_id]
            old_op = next(o for o in old.operations if o.operation_id == binding.operation_id)
            new_op = next(o for o in new.operations if o.operation_id == binding.operation_id)
            if binding.recipe_hash != old.semantic_hash() or binding.operation_hash != content_hash(
                old_op
            ):
                raise ValueError("旧共享规则内容绑定已失效，不能迁移")
            bindings.append(
                binding.model_copy(
                    update={
                        "recipe_hash": new.semantic_hash(),
                        "operation_hash": content_hash(new_op),
                    }
                )
            )
        updated = spec.model_copy(update={"bindings": tuple(bindings)})
        rules.append(
            rule.model_copy(
                update={
                    "group_compatibility_predicate": updated.model_dump_json(),
                    "evidence_refs": (*rule.evidence_refs, REVIEW_ID),
                }
            )
        )
    return tuple(rules)


def review_report(
    base: LoadedRelease,
    recipes: tuple[CanonicalRecipeModel, ...],
    rows: list[dict[str, object]],
) -> dict[str, object]:
    bindings = []
    for before, after in zip(base.snapshot.knowledge.recipes, recipes, strict=True):
        restored = after.model_copy(
            update={
                "recipe_version": before.recipe_version,
                "operations": before.operations,
                "approval": before.approval,
                "review_status": before.review_status,
                "review_patch_refs": before.review_patch_refs,
            }
        )
        if restored != before:
            raise ValueError("层位修订意外改变原菜谱事实")
        for old_op, new_op in zip(before.operations, after.operations, strict=True):
            if (
                new_op.model_copy(update={"resource_requirements": old_op.resource_requirements})
                != old_op
            ):
                raise ValueError("层位修订意外改变原工序或时长")
            for old_use, new_use in zip(
                old_op.resource_requirements, new_op.resource_requirements, strict=True
            ):
                if (
                    new_use.model_copy(
                        update={
                            "conflict_policy": old_use.conflict_policy,
                            "evidence_refs": old_use.evidence_refs,
                        }
                    )
                    != old_use
                ):
                    raise ValueError("层位修订意外改变设备配置或物理映射")
        bindings.append(
            {
                "recipe_id": before.recipe_id.root,
                "name": before.name,
                "before_semantic_hash": before.semantic_hash(),
                "after_semantic_hash": after.semantic_hash(),
                "approval_changed": after.approval != before.approval,
                "review_id": after.approval.review_id if after.approval else None,
            }
        )
    return {
        "review_id": REVIEW_ID,
        "actor_kind": "DELEGATED_AGENT",
        "base_release_id": BASE_ID,
        "base_snapshot_id": base.snapshot.snapshot_id,
        "base_content_hash": base.snapshot.content_hash,
        "new_release_id": RELEASE_ID,
        "review_scope": "用户确认的两独立三层腔体；同温同状态层位复用；原工艺事实逐项保留。",
        "process_duration_changes": 0,
        "temperature_mode_humidity_changes": 0,
        "new_kitchen_measurements": False,
        "formal_human_review_complete": False,
        "tests_run": False,
        "processing_rule_version": base.snapshot.knowledge.scope.rule_version,
        "recipe_count": len(recipes),
        "changed_recipe_count": sum(r["approval_changed"] is True for r in bindings),
        "reservation_count": len(rows),
        "layer_reuse_reservation_count": sum(r["policy_after"] == "STATE_COMPATIBLE" for r in rows),
        "exclusive_reservation_count": sum(r["policy_after"] == "UNARY" for r in rows),
        "recipe_bindings": bindings,
        "reservation_decisions": rows,
    }


def main() -> None:
    root = ROOT
    releases = root / RELEASE_ROOT
    base = load_release(releases, read_release_ref(releases, BASE_ID))
    source = base.snapshot.knowledge
    auth = json.loads((root / AUTH_PATH).read_bytes())
    if (auth["base_release_id"], auth["base_content_hash"]) != (BASE_ID, source.content_hash):
        raise ValueError("授权与不可变来源身份不匹配")
    if auth["actor_kind"] != "DELEGATED_AGENT" or auth["new_kitchen_measurements"]:
        raise ValueError("代理审核身份或实测标记不正确")
    confirmed = {d["device_instance_id"]: d for d in auth["confirmed_devices"]}
    actual = {
        d.device_instance_id: d for d in source.scope.devices if d.device_instance_id in DEVICES
    }
    if set(confirmed) != DEVICES or set(actual) != DEVICES:
        raise ValueError("用户确认的两个真实设备与来源不一致")
    for identity, device in actual.items():
        if (
            device.competition_key
            != (confirmed[identity]["physical_resource_id"], confirmed[identity]["component_id"])
            or confirmed[identity]["capacity"] != 3
        ):
            raise ValueError("三层容量不能覆盖已有物理设备映射")
    auth_id = auth["authorization_id"]
    decisions, rows = reservation_decisions(source)
    recipes = update_recipes(
        source, decisions, auth_id, auth["reviewer"], datetime.fromisoformat(auth["recorded_at"])
    )
    rules = update_rules(source, recipes)
    report = review_report(base, recipes, rows)
    write_immutable(root / REVIEW_PATH, encode(report))
    old_policy = json.loads((root / "data/policies/p6-cook-continuous-v1.json").read_bytes())
    new_policy = {
        **old_policy,
        "policy_version": "p6-cook-layered-v1",
        "strict_together_batch": False,
    }
    write_immutable(root / POLICY_PATH, encode(new_policy))
    added_artifacts = tuple(
        artifact(root, p)
        for p in (AUTH_PATH, REVIEW_PATH, Path("scripts/publish_layered_devices.py"), POLICY_PATH)
    )
    additions = tuple(
        ProvenanceRecord(
            provenance_id=ident,
            origin=origin,
            source_file=ref.path,
            source_hash=ref.sha256,
            record_id=ident,
            field_path=field,
            text_span=text,
            artifact_ref=ref,
            review_status=ReviewStatus.APPROVED,
            approved_review_ref=REVIEW_ID,
        )
        for ident, origin, ref, field, text in (
            (
                auth_id,
                "SOURCE_EXPLICIT",
                added_artifacts[0],
                "/user_messages",
                "；".join(auth["user_messages"]),
            ),
            (
                REVIEW_ID,
                "MODEL_SUGGESTION",
                added_artifacts[1],
                "/reservation_decisions",
                report["review_scope"],
            ),
        )
    )
    provenance = (*source.provenance, *additions)
    contexts: list[RecipeSchedulingContext] = []
    for context in source.scope.recipe_contexts:
        reservations = tuple(
            r.model_copy(update={"policy": ConflictPolicy.STATE_COMPATIBLE})
            if decisions.get((context.recipe_id.root, r.reservation_id), False)
            else r
            for r in context.resource_reservations
        )
        contexts.append(context.model_copy(update={"resource_reservations": reservations}))
    recipe_map = {r.recipe_id: r for r in recipes}
    devices = tuple(
        d.model_copy(
            update={
                "capacity": 3,
                "conflict_policy": ConflictPolicy.STATE_COMPATIBLE,
                "evidence_refs": (*d.evidence_refs, auth_id, REVIEW_ID),
            }
        )
        if d.device_instance_id in DEVICES
        else d
        for d in source.scope.devices
    )
    scope = source.scope.model_copy(
        update={
            "devices": devices,
            "recipe_contexts": tuple(contexts),
            "required_recipes": tuple(
                recipe_map[r.recipe_id] for r in source.scope.required_recipes
            ),
            "evidence_ids": tuple(p.provenance_id for p in provenance),
        }
    )
    validation = validate_knowledge(recipes, source.profiles, rules, scope)
    if not validation.valid:
        raise ValueError("知识发布门未通过：" + validation.model_dump_json())
    updated = ReviewedRelease(
        release_id=RELEASE_ID,
        knowledge_version=VERSION,
        recipes=recipes,
        profiles=source.profiles,
        rules=rules,
        scope=scope,
        provenance=provenance,
        source_artifacts=(*source.source_artifacts, *added_artifacts),
        validation=validation,
    )
    write_immutable(
        root / OUTPUT / "reviewed_release.json", encode(updated.model_dump(mode="json"))
    )
    staging = root / OUTPUT / "source-inputs"
    original_paths = {a.path for a in source.source_artifacts}
    for ref in updated.source_artifacts:
        origin = releases / BASE_ID if ref.path in original_paths else root
        write_immutable(staging / ref.path, ref.verify(origin).read_bytes())
    reference = publish_release(
        ReleaseBundle(
            build=export_canonical_snapshot(updated),
            source_root=staging,
            replays=base.manifest.replays,
            archive_root=releases / BASE_ID / "extraction",
        ),
        releases,
    )
    write_immutable(root / OUTPUT / "publication.json", encode(reference.model_dump(mode="json")))
    print(reference.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
