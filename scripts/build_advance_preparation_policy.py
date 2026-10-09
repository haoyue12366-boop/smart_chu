"""从固定100菜逐项绑定已核对的开工前准备，写入新的策略和审计文件。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from app.domain.advance_preparation import AdvancePreparationRule
from app.domain.base import content_hash
from app.domain.policy import SchedulingPolicy
from app.knowledge.loader import read_release_ref
from app.knowledge.repository import SnapshotKnowledgeRepository
from app.runtime.advance_preparation import checked_rules

ROOT = Path(__file__).resolve().parents[1]
# 2026-10-08 按用户确认逐项核对。这里是具体工序白名单，在线不按中文关键词筛选。
# 冷却、发酵和需要烧水的浸泡保留；同名菜各自使用原始 recipe_id。
TERMINALS = {
    "5d54bae2a9114174727c8b20": ("op_005_01",),
    "6510fb3545256c3ad7c25c9c": ("op_007_01",),
    "5c8646fe98d5bc7e184a90bc": ("op_002_01",),
    "61e6c51fec6e1d65587067e1": ("op_001_01",),
    "6577d480c458a177d8438637": ("op_004_01",),
    "65433a4245256c3ad7c2697f": ("op_004_01",),
    "58e70ae1a3fd4a750f4b75af": ("op_003_01",),
    "59cc89b47d21be2a79172c98": ("op_003_01",),
    "661369554b3d03197733ac5a": ("op_003_01", "op_005_01"),
    "58e70b129f6429675ec80601": ("op_003_01",),
    "5c875d0b98d5bc7e184a9166": ("op_004_01",),
    "5caf2ea3e78bfc455f2d5cad": ("op_1000_01",),
    "5f5ecdaadad9417a1b793a9f": ("op_005_01",),
    "66715324bfbee338853895c7": ("op_003_01",),
    "65433d5545256c3ad7c26fbb": ("op_002_01",),
    "65795251c458a177d8438e01": ("op_002_02", "op_005_01"),
    "5fe1975b5f8f38795ea6fe81": ("op_003_01",),
    "5fc5f9e15f8f38795ea6fca6": ("op_005_01",),
    "58e70b0e9f6429675ec805d8": ("op_003_01",),
    "5cc7e0404a21a4301960aef1": ("op_002_01", "op_1000_01"),
    "5caf2ec7e78bfc455f2d5cb0": ("op_003_01",),
    "668759f0bfbee3388538968e": ("op_002_01",),
    "5caf2ddbe78bfc455f2d5c96": ("op_002_01", "op_007_01"),
    "5c86417e98d5bc7e184a90a8": ("op_1000_01",),
    "5d664a986601a865649f9aba": ("op_013_01",),
    "5c81cf416dc6e123a037a1f0": ("op_1000_01", "op_1001_01"),
    "64f5510a255d3b02f695fde1": ("op_004_01",),
    "5fe1972d5f8f38795ea6fe7b": ("op_005_01",),
    "65f7f4ee85eb6c79dfb992ff": ("op_001_01",),
    "5cc7eb394a21a4301960af00": ("op_001_01",),
    "5c871bef98d5bc7e184a90dd": ("op_003_01",),
    "66cec69b1142545e3856e07e": ("op_1000_02", "op_003_01"),
    "662e07388b2aa2265e115477": ("op_001_01",),
    "6544c6c245256c3ad7c27455": ("op_003_01",),
    "6510fb7845256c3ad7c25cd8": ("op_008_01",),
    "65433d7145256c3ad7c270fb": ("op_004_01",),
    "6543408e45256c3ad7c2744b": ("op_002_01",),
    "5fe197255f8f38795ea6fe79": ("op_003_01",),
    "65795214c458a177d8438b22": ("op_010_01",),
    "66dea6480d586d528ee9dd7c": ("op_009_01", "op_012_01"),
    "5c82027a6dc6e123a037a209": ("op_001_01",),
    "6492a828933a4b7277dee6aa": ("op_003_01",),
}


def main() -> None:
    release_root = ROOT / "data/preparations/p4-v1/releases"
    reference = read_release_ref(release_root, "delegated-v3-layered-devices-v1-all")
    repository = SnapshotKnowledgeRepository(release_root)
    repository.load(reference)
    with repository.acquire(reference) as lease:
        knowledge = lease.select(
            tuple(recipe.recipe_id for recipe in lease.loaded.snapshot.knowledge.recipes)
        )
    old = SchedulingPolicy.model_validate_json(
        (ROOT / "data/policies/p6-cook-layered-v1.json").read_bytes()
    )
    rules = []
    audit = []
    contexts = {item.recipe_id: item for item in knowledge.recipe_contexts}
    excluded_rules = []
    for recipe in knowledge.recipes:
        operations = {operation.operation_id.root: operation for operation in recipe.operations}
        selected = set(TERMINALS.get(recipe.recipe_id.root, ()))
        scope = set(selected)
        while True:
            added = {
                dep.predecessor_id.root
                for dep in recipe.dependencies
                if dep.successor_id.root in scope
            }
            # 原图要求紧接完成的手工备料也随准备前置，不能截断零/有限间隔链。
            added.update(
                dep.successor_id.root
                for dep in recipe.dependencies
                if dep.predecessor_id.root in scope and dep.max_lag_sec is not None
            )
            if added <= scope:
                break
            scope.update(added)
        ordered = tuple(
            operation.operation_id
            for operation in recipe.operations
            if operation.operation_id.root in scope
        )
        excluded_reason = None
        context = contexts.get(recipe.recipe_id)
        anchors = (
            set(context.cooking_completion.operation_ids)
            if context and context.cooking_completion
            else set()
        )
        if selected and any(
            dep.predecessor_id.root in scope
            and dep.successor_id.root not in scope
            and dep.max_lag_sec is not None
            for dep in recipe.dependencies
        ):
            excluded_reason = "前置边界含原工艺最大间隔，保留原调度"
        if selected and anchors.intersection(ordered):
            excluded_reason = "前置范围含原出锅/可食用锚点，保留原调度"
        if selected and any(
            operations[key].action in {"HEAT", "PREHEAT", "UNLOAD", "FINISH", "UNKNOWN", "STIR"}
            or any(
                use.resource_type == "DEVICE" and use.physical_resource_id != "fridge_1"
                for use in operations[key].resource_requirements
            )
            for key in scope
        ):
            excluded_reason = "紧密前置闭包含热加工或厨具，保留原调度"
        if excluded_reason:
            excluded_rules.append(
                {"recipe_id": recipe.recipe_id.root, "name": recipe.name, "reason": excluded_reason}
            )
            selected = set()
            scope = set()
            ordered = ()
        if selected:
            rules.append(
                AdvancePreparationRule(
                    rule_id="advance-preparation-v1:" + recipe.recipe_id.root,
                    recipe_id=recipe.recipe_id,
                    recipe_hash=content_hash(recipe),
                    operation_ids=ordered,
                    description="；".join(
                        operations[op].description.rstrip("。；")
                        for op in TERMINALS[recipe.recipe_id.root]
                    ),
                    evidence_refs=(
                        "user:2026-10-08:advance-preparation-before-start",
                        "DELEGATED_AGENT:2026-10-08:advance-preparation-v1",
                        *recipe.provenance_refs,
                    ),
                )
            )
        for operation in recipe.operations:
            if operation.action not in {"WAIT", "MARINATE", "CHILL", "FREEZE"}:
                continue
            ancestors = {operation.operation_id.root}
            while True:
                extra = {
                    dep.predecessor_id.root
                    for dep in recipe.dependencies
                    if dep.successor_id.root in ancestors
                }
                if extra <= ancestors:
                    break
                ancestors.update(extra)
            blocked = [
                key
                for key in sorted(ancestors)
                if operations[key].action
                in {"HEAT", "PREHEAT", "UNLOAD", "FINISH", "UNKNOWN", "STIR"}
                or any(
                    use.resource_type == "DEVICE" and use.physical_resource_id != "fridge_1"
                    for use in operations[key].resource_requirements
                )
            ]
            audit.append(
                {
                    "recipe_id": recipe.recipe_id.root,
                    "name": recipe.name,
                    "recipe_hash": content_hash(recipe),
                    "operation_id": operation.operation_id.root,
                    "description": operation.description,
                    "action": operation.action,
                    "duration_sec": operation.duration.execution_sec,
                    "provenance_refs": operation.provenance_refs,
                    "advance_prepared": operation.operation_id.root in scope,
                    "blocked_ancestor_ids": blocked,
                    "reason": "用户策略：开工前已备好"
                    if operation.operation_id.root in scope
                    else "闭包含热加工或实际厨具"
                    if blocked
                    else excluded_reason or "冷却或发酵保留在本轮工艺",
                }
            )
    policy = old.model_copy(
        update={
            "policy_version": "p6-cook-prepared-v1",
            "advance_preparation_mode": "ASSUME_READY",
            "advance_preparation_rules": tuple(rules),
        }
    )
    policy = SchedulingPolicy.model_validate(policy.model_dump())
    for recipe in knowledge.recipes:
        checked_rules(recipe, policy)
    destination = ROOT / "data/policies/p6-cook-prepared-v1.json"
    evidence = ROOT / "data/verification/2026-10-08-reservation-spans/preparation-rule-audit.json"
    for path in (destination, evidence):
        if path.exists() and "--overwrite-draft" not in sys.argv:
            raise FileExistsError(f"新版本输出已存在，不覆盖：{path}")
    destination.write_text(policy.model_dump_json(indent=2) + "\n", encoding="utf-8")
    evidence.write_text(
        json.dumps(
            {
                "source_kind": "USER_POLICY_ASSUMPTION",
                "knowledge_release": reference.model_dump(mode="json"),
                "policy_version": policy.policy_version,
                "policy_hash": content_hash(policy),
                "recipes_scanned": len(knowledge.recipes),
                "recipes_with_preparation": len(rules),
                "prepared_operations": sum(len(rule.operation_ids) for rule in rules),
                "excluded_rules": excluded_rules,
                "prepared_cooking_anchor_count": 0,
                "minimum_duration_threshold": None,
                "operations": audit,
                "rule_operations": [
                    {
                        "recipe_id": recipe.recipe_id.root,
                        "name": recipe.name,
                        "operations": [
                            operation.model_dump(mode="json")
                            for operation in recipe.operations
                            if any(
                                rule.recipe_id == recipe.recipe_id
                                and operation.operation_id in rule.operation_ids
                                for rule in rules
                            )
                        ],
                    }
                    for recipe in knowledge.recipes
                    if recipe.recipe_id.root in TERMINALS
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "policy": str(destination),
                "recipes_scanned": len(knowledge.recipes),
                "rules": len(rules),
                "prepared_operations": sum(len(rule.operation_ids) for rule in rules),
                "excluded_rules": excluded_rules,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
