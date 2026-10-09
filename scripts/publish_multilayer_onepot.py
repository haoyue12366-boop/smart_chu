"""按原文与用户授权发布轻松一锅蒸固定双层预约，保留不可变旧发布。"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from pathlib import Path

from app.config import ROOT
from app.domain.base import ReviewStatus, content_hash
from app.domain.canonical_recipe import CanonicalRecipeModel, ReviewStamp
from app.domain.compatibility import GroupRuleSpec
from app.domain.knowledge_release import ReviewedRelease
from app.domain.policy import SchedulingPolicy
from app.domain.processing_rules import ProcessingRule
from app.domain.provenance import ProvenanceRecord
from app.domain.recipe_context import RecipeSchedulingContext
from app.domain.resources import ResourceUse
from app.knowledge.loader import load_release, read_release_ref
from app.pipeline.publish import ReleaseBundle, publish_release
from app.pipeline.snapshot_export import export_canonical_snapshot
from app.validation.knowledge import validate_knowledge
from scripts.publish_layered_devices import artifact, encode, write_immutable

BASE_ID = "delegated-v3-layered-devices-v1-all"
VERSION = "delegated-v3-multilayer-onepot-v1"
RELEASE_ID = VERSION + "-all"
RELEASE_ROOT = Path("data/preparations/p4-v1/releases")
OUTPUT = Path("data/preparations/multilayer-onepot-v1")
AUTH_PATH = OUTPUT / "authorizations/user-confirmation.json"
SOURCE_PATH = OUTPUT / "original-source.json"
REVIEW_PATH = OUTPUT / "review.json"
SCRIPT_PATH = Path("scripts/publish_multilayer_onepot.py")
BASE_POLICY_PATH = Path("data/policies/p6-cook-prepared-v1.json")
POLICY_PATH = Path("data/policies/p6-cook-prepared-multilayer-v1.json")
REVIEW_ID = "DELEGATED_AGENT:2026-10-08:multilayer-onepot-v1"
SOURCE_ID = "multilayer-onepot-v1:original-tray-placement"
RECIPE_ID = "66715324bfbee338853895c7"
RESERVATION_ID = RECIPE_ID + ":steam:2"
LOAD_ID = "op_013_01"
EXISTING_MEMBERS = ("op_014_01", "op_014_02", "op_014_03", "op_1000_01")


def fixed_use(use: ResourceUse, authorization_id: str) -> ResourceUse:
    return ResourceUse.model_validate(
        use.model_copy(
            update={
                "units": 2,
                "occupied_layer_indices": (1, 3),
                "evidence_refs": (*use.evidence_refs, authorization_id, SOURCE_ID, REVIEW_ID),
            }
        ).model_dump()
    )


def updated_recipe(
    before: CanonicalRecipeModel, authorization: dict[str, object]
) -> CanonicalRecipeModel:
    operations = []
    auth_id = str(authorization["authorization_id"])
    reference_use = next(
        use
        for operation in before.operations
        if operation.operation_id.root == EXISTING_MEMBERS[0]
        for use in operation.resource_requirements
        if use.resource_id == "steam_oven_1"
    )
    for operation in before.operations:
        uses = operation.resource_requirements
        if operation.operation_id.root in EXISTING_MEMBERS:
            uses = tuple(
                fixed_use(use, auth_id) if use.resource_id == "steam_oven_1" else use
                for use in uses
            )
        elif operation.operation_id.root == LOAD_ID:
            if any(use.resource_id == "steam_oven_1" for use in uses):
                raise ValueError("基线装盘操作已有蒸箱资源，必须重新审核修订范围")
            uses = (*uses, fixed_use(reference_use, auth_id))
        operations.append(operation.model_copy(update={"resource_requirements": uses}))
    draft = before.model_copy(
        update={
            "recipe_version": before.recipe_version + "+multilayer-onepot-v1",
            "operations": tuple(operations),
            "review_status": ReviewStatus.NEEDS_REVIEW,
            "approval": None,
        }
    )
    review_id = REVIEW_ID + ":" + RECIPE_ID
    approved = draft.model_copy(
        update={
            "review_status": ReviewStatus.APPROVED,
            "review_patch_refs": (*before.review_patch_refs, review_id),
            "approval": ReviewStamp(
                review_id=review_id,
                reviewer=str(authorization["reviewer"]),
                reviewed_at=datetime.fromisoformat(str(authorization["recorded_at"])),
                evidence_refs=(auth_id, SOURCE_ID, REVIEW_ID),
                approved_content_hash=draft.semantic_hash(),
            ),
        }
    )
    return CanonicalRecipeModel.model_validate_json(approved.model_dump_json())


def rebind_rules(
    source: ReviewedRelease, before: CanonicalRecipeModel, after: CanonicalRecipeModel
) -> tuple[ProcessingRule, ...]:
    rules = []
    for rule in source.rules:
        spec = GroupRuleSpec.model_validate_json(rule.group_compatibility_predicate)
        bindings = []
        for binding in spec.bindings:
            if binding.recipe_id == before.recipe_id:
                old_op = next(
                    o for o in before.operations if o.operation_id == binding.operation_id
                )
                new_op = next(o for o in after.operations if o.operation_id == binding.operation_id)
                if binding.recipe_hash != before.semantic_hash() or binding.operation_hash != (
                    content_hash(old_op)
                ):
                    raise ValueError("原共享规则绑定失效，不能迁移")
                binding = binding.model_copy(
                    update={
                        "recipe_hash": after.semantic_hash(),
                        "operation_hash": content_hash(new_op),
                    }
                )
            bindings.append(binding)
        if tuple(bindings) != spec.bindings:
            rule = rule.model_copy(
                update={
                    "group_compatibility_predicate": spec.model_copy(
                        update={"bindings": tuple(bindings)}
                    ).model_dump_json(),
                    "evidence_refs": (*rule.evidence_refs, REVIEW_ID),
                }
            )
        rules.append(rule)
    return tuple(rules)


def rebind_policy(before: CanonicalRecipeModel, after: CanonicalRecipeModel) -> SchedulingPolicy:
    original = SchedulingPolicy.model_validate_json((ROOT / BASE_POLICY_PATH).read_bytes())
    rules = []
    for rule in original.advance_preparation_rules:
        if rule.recipe_id == before.recipe_id:
            if rule.recipe_hash != content_hash(before):
                raise ValueError("原前置准备规则绑定失效，不能迁移")
            rule = rule.model_copy(update={"recipe_hash": content_hash(after)})
        rules.append(rule)
    return original.model_copy(
        update={
            "policy_version": "p6-cook-prepared-multilayer-v1",
            "advance_preparation_rules": tuple(rules),
        }
    )


def verify_scope(
    before: CanonicalRecipeModel, after: CanonicalRecipeModel
) -> list[dict[str, object]]:
    restored = after.model_copy(
        update={
            "recipe_version": before.recipe_version,
            "operations": before.operations,
            "review_patch_refs": before.review_patch_refs,
            "approval": before.approval,
        }
    )
    if restored != before:
        raise ValueError("修订越过原菜谱资源绑定范围")
    rows = []
    for old, new in zip(before.operations, after.operations, strict=True):
        if new.model_copy(update={"resource_requirements": old.resource_requirements}) != old:
            raise ValueError("不能修改任何原工序、物料、人工或时长")
        old_other = tuple(u for u in old.resource_requirements if u.resource_id != "steam_oven_1")
        new_other = tuple(u for u in new.resource_requirements if u.resource_id != "steam_oven_1")
        if old_other != new_other:
            raise ValueError("不能修改人工或其他设备")
        if old == new:
            continue
        use = next(u for u in new.resource_requirements if u.resource_id == "steam_oven_1")
        if use.occupied_layer_indices != (1, 3) or use.units != 2:
            raise ValueError("固定双层声明不完整")
        old_steam = [u for u in old.resource_requirements if u.resource_id == "steam_oven_1"]
        if (
            old_steam
            and use.model_copy(
                update={
                    "units": 1,
                    "occupied_layer_indices": (),
                    "evidence_refs": old_steam[0].evidence_refs,
                }
            )
            != old_steam[0]
        ):
            raise ValueError("不能修改蒸箱配置或物理映射")
        rows.append(
            {
                "operation_id": old.operation_id.root,
                "action": old.action.value,
                "duration_sec": old.duration.execution_sec,
                "change": "ADD_SOURCE_LOAD_OCCUPANCY" if not old_steam else "FIX_TWO_LAYERS",
                "occupied_layer_indices": [1, 3],
                "units": 2,
                "configuration": [v.model_dump(mode="json") for v in use.configuration],
            }
        )
    if {r["operation_id"] for r in rows} != {LOAD_ID, *EXISTING_MEMBERS}:
        raise ValueError("修订未严格覆盖放入至取出的完整双层预约")
    return rows


def main() -> None:
    releases = ROOT / RELEASE_ROOT
    base = load_release(releases, read_release_ref(releases, BASE_ID))
    source = base.snapshot.knowledge
    auth = json.loads((ROOT / AUTH_PATH).read_bytes())
    if (auth["base_release_id"], auth["base_content_hash"]) != (BASE_ID, source.content_hash):
        raise ValueError("授权与不可变来源身份不匹配")
    if auth["actor_kind"] != "DELEGATED_AGENT" or auth["new_kitchen_measurements"]:
        raise ValueError("代理审核身份或实测标记不正确")
    if auth["recipe_id"] != RECIPE_ID or auth["occupied_layer_indices"] != [1, 3]:
        raise ValueError("授权必须明确目标菜谱和固定层位")
    source_ref = artifact(ROOT, Path("recipes_100.csv"))
    row = next(
        r
        for r in csv.DictReader(
            io.StringIO((ROOT / source_ref.path).read_text(encoding="utf-8-sig"))
        )
        if r["菜谱id"] == RECIPE_ID
    )
    if "蒸烤架放入第1层，蒸烤盘放入第3层" not in row["烹饪步骤"]:
        raise ValueError("固定双层缺少原始菜谱依据")
    write_immutable(
        ROOT / SOURCE_PATH,
        encode({"original_artifact": source_ref.model_dump(mode="json"), "original_record": row}),
    )
    before = next(r for r in source.recipes if r.recipe_id.root == RECIPE_ID)
    after = updated_recipe(before, auth)
    changed_operations = verify_scope(before, after)
    recipes = tuple(after if r.recipe_id == before.recipe_id else r for r in source.recipes)
    rules = rebind_rules(source, before, after)
    policy = rebind_policy(before, after)
    write_immutable(ROOT / POLICY_PATH, encode(policy.model_dump(mode="json")))
    contexts: list[RecipeSchedulingContext] = []
    for context in source.scope.recipe_contexts:
        if context.recipe_id == before.recipe_id:
            reservation = next(
                r for r in context.resource_reservations if r.reservation_id == RESERVATION_ID
            )
            if tuple(m.root for m in reservation.members) != EXISTING_MEMBERS:
                raise ValueError("原完整预约成员变化，必须重新审核")
            load_id = next(
                o.operation_id for o in before.operations if o.operation_id.root == LOAD_ID
            )
            updated_reservation = reservation.model_copy(
                update={"members": (load_id, *reservation.members)}
            )
            context = context.model_copy(
                update={
                    "resource_reservations": tuple(
                        updated_reservation if r == reservation else r
                        for r in context.resource_reservations
                    )
                }
            )
        contexts.append(context)
    old_context = next(c for c in source.scope.recipe_contexts if c.recipe_id == before.recipe_id)
    new_context = next(c for c in contexts if c.recipe_id == before.recipe_id)
    report = {
        "review_id": REVIEW_ID,
        "actor_kind": "DELEGATED_AGENT",
        "base_release_id": BASE_ID,
        "base_snapshot_id": base.snapshot.snapshot_id,
        "base_content_hash": source.content_hash,
        "new_release_id": RELEASE_ID,
        "recipe_id": RECIPE_ID,
        "review_scope": auth["review_scope"],
        "recipe_count": len(recipes),
        "changed_recipe_count": sum(a != b for a, b in zip(source.recipes, recipes, strict=True)),
        "changed_context_count": sum(
            a != b for a, b in zip(source.scope.recipe_contexts, contexts, strict=True)
        ),
        "before_semantic_hash": before.semantic_hash(),
        "after_semantic_hash": after.semantic_hash(),
        "before_recipe_hash": content_hash(before),
        "after_recipe_hash": content_hash(after),
        "before_context_hash": content_hash(old_context),
        "after_context_hash": content_hash(new_context),
        "reservation_before": next(
            r.model_dump(mode="json")
            for r in old_context.resource_reservations
            if r.reservation_id == RESERVATION_ID
        ),
        "reservation_after": next(
            r.model_dump(mode="json")
            for r in new_context.resource_reservations
            if r.reservation_id == RESERVATION_ID
        ),
        "changed_operations": changed_operations,
        "process_duration_changes": 0,
        "temperature_mode_humidity_changes": 0,
        "human_changes": 0,
        "advance_preparation_rule_count": len(policy.advance_preparation_rules),
        "advance_preparation_operation_count": sum(
            len(r.operation_ids) for r in policy.advance_preparation_rules
        ),
        "advance_preparation_change": "仅轻松一锅蒸更新recipe_hash；42道菜准备规则及原操作集合保留",
        "new_kitchen_measurements": False,
        "formal_human_review_complete": False,
    }
    write_immutable(ROOT / REVIEW_PATH, encode(report))
    added_artifacts = tuple(
        artifact(ROOT, p)
        for p in (AUTH_PATH, SOURCE_PATH, REVIEW_PATH, SCRIPT_PATH, BASE_POLICY_PATH, POLICY_PATH)
    )
    additions = tuple(
        ProvenanceRecord(
            provenance_id=identity,
            origin=origin,
            source_file=ref.path,
            source_hash=ref.sha256,
            record_id=RECIPE_ID,
            field_path=field,
            text_span=text,
            artifact_ref=ref,
            review_status=ReviewStatus.APPROVED,
            approved_review_ref=REVIEW_ID,
        )
        for identity, origin, ref, field, text in (
            (
                auth["authorization_id"],
                "SOURCE_EXPLICIT",
                added_artifacts[0],
                "/user_messages",
                "；".join(auth["user_messages"]),
            ),
            (
                SOURCE_ID,
                "SOURCE_EXPLICIT",
                added_artifacts[1],
                "/original_record/烹饪步骤",
                row["烹饪步骤"],
            ),
            (
                REVIEW_ID,
                "MODEL_SUGGESTION",
                added_artifacts[2],
                "/changed_operations",
                auth["review_scope"],
            ),
        )
    )
    provenance = (*source.provenance, *additions)
    scope = source.scope.model_copy(
        update={
            "recipe_contexts": tuple(contexts),
            "required_recipes": tuple(
                after if r.recipe_id == before.recipe_id else r
                for r in source.scope.required_recipes
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
        ROOT / OUTPUT / "reviewed_release.json", encode(updated.model_dump(mode="json"))
    )
    staging = ROOT / OUTPUT / "source-inputs"
    original_paths = {a.path for a in source.source_artifacts}
    for ref in updated.source_artifacts:
        origin = releases / BASE_ID if ref.path in original_paths else ROOT
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
    write_immutable(ROOT / OUTPUT / "publication.json", encode(reference.model_dump(mode="json")))
    print(reference.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
