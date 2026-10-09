"""固定发布及显式合成输入；不覆盖知识发布、不注入计划完成事实。"""

import json
from datetime import datetime
from pathlib import Path

from app.domain.compatibility import GroupRuleSpec
from app.domain.ids import EventId, RecipeId
from app.domain.runtime_snapshot import DeviceState, RuntimeSnapshot
from app.domain.scheduling_problem import RecipeInstance
from app.domain.time import TimeOrigin
from app.knowledge.loader import read_release_ref
from app.knowledge.repository import SnapshotKnowledgeRepository

ROOT = Path(__file__).resolve().parents[1]


def load_cases():
    return json.loads(
        (ROOT / "benchmarks/scenarios/shared_cases.json").read_text(encoding="utf-8")
    )["cases"]


def load_published_knowledge():
    root = ROOT / "data/preparations/p3-thermal-v1/releases"
    ref = read_release_ref(root, "development-v3-p3-thermal-v1-all")
    repository = SnapshotKnowledgeRepository(root)
    repository.load(ref)
    ids = tuple(
        RecipeId(i) for i in dict.fromkeys(i for c in load_cases() for i in c["recipe_ids"])
    )
    return repository.select(ids)


def case_inputs(base, case):
    recipes = tuple(
        r for rid in case["recipe_ids"] for r in base.recipes if r.recipe_id.root == rid
    )
    if len(recipes) != len(case["recipe_ids"]):
        raise ValueError("实验引用发布之外的菜谱")
    knowledge = base.model_copy(
        update={
            "recipes": recipes,
            "recipe_contexts": tuple(
                c for c in base.recipe_contexts if c.recipe_id.root in case["recipe_ids"]
            ),
        }
    )
    if case["source_kind"] == "SYNTHETIC_DEPENDENCY":
        amended = []
        for recipe in recipes:
            if recipe.recipe_id.root == case["late_recipe_id"]:
                dependencies = tuple(
                    d.model_copy(
                        update={
                            "min_lag_sec": case["minimum_wait_sec"],
                            "max_lag_sec": None,
                            "reason": "SYNTHETIC waiting boundary for P3 ablation",
                        }
                    )
                    if d.successor_id.root == case["wait_before_operation_id"]
                    else d
                    for d in recipe.dependencies
                )
                if dependencies == recipe.dependencies:
                    raise ValueError("合成等待没有匹配到源依赖")
                recipe = recipe.model_copy(update={"dependencies": dependencies})
            amended.append(recipe)
        by_id = {r.recipe_id: r for r in amended}
        rules = []
        for rule in knowledge.rules:
            spec = GroupRuleSpec.model_validate_json(rule.group_compatibility_predicate)
            bindings = tuple(
                b.model_copy(update={"recipe_hash": by_id[b.recipe_id].semantic_hash()})
                if b.recipe_id in by_id
                else b
                for b in spec.bindings
            )
            rules.append(
                rule.model_copy(
                    update={
                        "group_compatibility_predicate": spec.model_copy(
                            update={"bindings": bindings}
                        ).model_dump_json()
                    }
                )
            )
        knowledge = knowledge.model_copy(update={"recipes": tuple(amended), "rules": tuple(rules)})
    menu = tuple(
        RecipeInstance(recipe_instance_id=f"ablation-{i}", recipe_id=r.recipe_id, name=r.name)
        for i, r in enumerate(knowledge.recipes)
    )
    runtime = RuntimeSnapshot(
        session_id="p3-ablation",
        state_revision=0,
        current_plan_version=0,
        knowledge_version=base.release.knowledge_version,
        rule_version=base.release.rule_version,
        snapshot_id=base.release.snapshot_id,
        time_origin=TimeOrigin(start_at=datetime.fromisoformat("2026-09-29T08:00:00+08:00")),
        now_offset_sec=0,
        execution_mode="SIMULATED",
    )
    if case["source_kind"] == "SYNTHETIC_RUNTIME":
        device = next(
            d for d in knowledge.devices if d.device_instance_id == case["unavailable_device_id"]
        )
        runtime = runtime.model_copy(
            update={
                "event_refs": (EventId("synthetic-device-unavailable"),),
                "device_states": (
                    DeviceState(
                        device_instance_id=device.device_instance_id,
                        physical_resource_id=device.physical_resource_id,
                        component_id=device.component_id,
                        availability_status="UNAVAILABLE",
                        occupancy_status="FREE",
                        observed_at=runtime.time_origin.start_at,
                        source="SIMULATED",
                    ),
                ),
            }
        )
    return knowledge, menu, RuntimeSnapshot.model_validate(runtime.model_dump())
