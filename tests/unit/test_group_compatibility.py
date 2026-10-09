"""真实发布来源的兼容判断；审核参数为显式合成/委托开发数据。"""

import pytest

from app.domain.base import content_hash
from app.domain.compatibility import GroupBinding, GroupContext, GroupRuleSpec
from app.domain.processing_rules import ProcessingRule
from app.domain.runtime_snapshot import ExecutionRecord
from app.knowledge.rules import RuleEngine
from tests.compiler_support import menu_for, published_knowledge, runtime


def fixture_group(thermal=False):
    from app.compiler.instantiate import instantiate

    k = published_knowledge()
    ids = {"5c8200d96dc6e123a037a202", "5fe197175f8f38795ea6fe77"}
    recipes = tuple(r for r in k.recipes if r.recipe_id.root in ids)
    menu = menu_for(*recipes)
    inst = instantiate(menu, k, runtime(k))
    oid = "op_008_03" if thermal else "op_001_01"
    tasks = tuple(t for t in inst.tasks if t.operation_id.root == oid)
    bindings = tuple(
        GroupBinding(
            recipe_id=r.recipe_id,
            operation_id=oid,
            recipe_hash=r.semantic_hash(),
            operation_hash=content_hash(t.operation),
            processing_spec="普通蒸100℃" if thermal else "干腐竹6cm切段及原步骤加水",
            configuration_keys=("steam_oven_1:100:普通蒸",) if thermal else ("human_1:cut",),
        )
        for r, t in zip(recipes, tasks, strict=True)
    )
    spec = GroupRuleSpec(
        bindings=bindings,
        duration_sec=720 if thermal else 240,
        authority="DELEGATED_DEVELOPMENT_ESTIMATE",
        authorization_ref="delegated-review-2026-09-28",
        scope_note="合成测试：仅这两个原配方实例，不外推数量",
    )
    rule = ProcessingRule(
        rule_id="test-group",
        rule_version=k.release.rule_version,
        kind="STRICT_TOGETHER" if thermal else "SHARED_PREP",
        group_compatibility_predicate=spec.model_dump_json(),
        evidence_refs=("test:explicit-synthetic-rule",),
        review_status="NEEDS_REVIEW",
    )
    k = k.model_copy(update={"rules": (rule,)})
    return tasks, GroupContext(
        knowledge=k, runtime=runtime(k), menu=menu, allow_delegated_estimates=True
    )


def test_bound_delegated_rule_is_accepted_only_in_development():
    members, ctx = fixture_group()
    decision = RuleEngine().evaluate_group(members, ctx)
    assert decision.compatible
    assert decision.rule_refs == ("test-group",)
    assert decision.common_configuration_keys == ("human_1:cut",)
    assert (
        not RuleEngine()
        .evaluate_group(members, ctx.model_copy(update={"allow_delegated_estimates": False}))
        .compatible
    )
    formal = ctx.knowledge.model_copy(
        update={"release": ctx.knowledge.release.model_copy(update={"release_kind": "competition"})}
    )
    assert (
        not RuleEngine()
        .evaluate_group(members, ctx.model_copy(update={"knowledge": formal}))
        .compatible
    )


def test_same_local_operation_id_cannot_confuse_different_recipes():
    members, ctx = fixture_group()
    assert members[0].operation_id == members[1].operation_id
    assert not RuleEngine().evaluate_group((members[0], members[0]), ctx).compatible


@pytest.mark.parametrize("field,value", [("description", "腐竹切丝"), ("material_inputs", ())])
def test_changed_shape_or_quantity_invalidates_bound_rule(field, value):
    members, ctx = fixture_group()
    changed = members[0].model_copy(
        update={"operation": members[0].operation.model_copy(update={field: value})}
    )
    assert not RuleEngine().evaluate_group((changed, members[1]), ctx).compatible


@pytest.mark.parametrize("status", ["RUNNING", "COMPLETED", "FAILED", "CANCELLED"])
def test_started_finished_or_failed_members_cannot_be_merged(status):
    members, ctx = fixture_group()
    now = ctx.runtime.time_origin.start_at
    record = ExecutionRecord(
        execution_id="started",
        task_ids=(members[0].task_id,),
        status=status,
        source="SIMULATED",
        event_refs=("event-1",),
        started_at=now,
        finished_at=now if status in {"COMPLETED", "FAILED"} else None,
    )
    ctx = ctx.model_copy(
        update={"runtime": ctx.runtime.model_copy(update={"executions": (record,)})}
    )
    assert not RuleEngine().evaluate_group(members, ctx).compatible


def test_unknown_materials_and_impossible_window_are_rejected():
    members, ctx = fixture_group()
    assert (
        not RuleEngine()
        .evaluate_group(
            members, ctx.model_copy(update={"unknown_material_task_ids": (members[0].task_id,)})
        )
        .compatible
    )
    changed = members[1].model_copy(update={"earliest_start_sec": 500, "latest_end_sec": 600})
    assert not RuleEngine().evaluate_group((members[0], changed), ctx).compatible
    late = members[1].model_copy(update={"earliest_start_sec": 500})
    assert RuleEngine().evaluate_group((members[0], late), ctx).compatible


def test_disjoint_configuration_sets_are_rejected():
    members, ctx = fixture_group()
    rule = ctx.knowledge.rules[0]
    spec = GroupRuleSpec.model_validate_json(rule.group_compatibility_predicate)
    # Two groups with disjoint approved configurations cannot be merged.
    bindings = (
        spec.bindings[0].model_copy(update={"configuration_keys": ("a",)}),
        spec.bindings[1].model_copy(update={"configuration_keys": ("b",)}),
    )
    rule = rule.model_copy(
        update={
            "group_compatibility_predicate": spec.model_copy(
                update={"bindings": bindings}
            ).model_dump_json()
        }
    )
    ctx = ctx.model_copy(update={"knowledge": ctx.knowledge.model_copy(update={"rules": (rule,)})})
    assert not RuleEngine().evaluate_group(members, ctx).compatible


def test_pairwise_intersections_do_not_prove_three_way_compatibility():
    from app.compiler.instantiate import instantiate
    from app.domain.ids import RecipeId

    members, ctx = fixture_group()
    recipe_ids = {i.recipe_id for i in ctx.menu}
    recipes = tuple(r for r in ctx.knowledge.recipes if r.recipe_id in recipe_ids)
    third = recipes[0].model_copy(update={"recipe_id": RecipeId("synthetic-third")})
    recipes = (*recipes, third)
    menu = menu_for(*recipes)
    k = ctx.knowledge.model_copy(update={"recipes": recipes})
    inst = instantiate(menu, k, runtime(k))
    members = tuple(t for t in inst.tasks if t.operation_id.root == "op_001_01")
    bindings = tuple(
        GroupBinding(
            recipe_id=r.recipe_id,
            operation_id=t.operation_id,
            recipe_hash=r.semantic_hash(),
            operation_hash=content_hash(t.operation),
            processing_spec="SYNTHETIC_SAME_SPEC",
            configuration_keys=keys,
        )
        for r, t, keys in zip(recipes, members, (("a", "b"), ("b", "c"), ("a", "c")), strict=True)
    )
    spec = GroupRuleSpec(
        bindings=bindings,
        duration_sec=240,
        authority="DELEGATED_DEVELOPMENT_ESTIMATE",
        authorization_ref="synthetic",
        scope_note="三组两两交集，整组为空",
    )
    rule = k.rules[0].model_copy(update={"group_compatibility_predicate": spec.model_dump_json()})
    k = k.model_copy(update={"rules": (rule,)})
    ctx = ctx.model_copy(update={"knowledge": k, "menu": menu})
    decision = RuleEngine().evaluate_group(members, ctx)
    assert not decision.compatible
    assert any("全组没有共同配置" in reason for reason in decision.reasons)


def test_ancestor_and_descendant_cannot_be_synchronized():
    from app.compiler.instantiate import instantiate

    _, ctx = fixture_group()
    inst = instantiate(ctx.menu, ctx.knowledge, ctx.runtime)
    first = ctx.menu[0].recipe_instance_id
    members = tuple(
        t
        for t in inst.tasks
        if t.recipe_instance_id == first and t.operation_id.root in {"op_001_01", "op_004_01"}
    )
    decision = RuleEngine().evaluate_group(members, ctx)
    assert not decision.compatible
    assert any("祖先后继" in reason for reason in decision.reasons)


def test_retired_rules_and_changed_rule_versions_are_not_used():
    members, ctx = fixture_group()
    for changes in ({"review_status": "RETIRED"}, {"rule_version": "other"}):
        k = ctx.knowledge.model_copy(
            update={"rules": (ctx.knowledge.rules[0].model_copy(update=changes),)}
        )
        assert (
            not RuleEngine()
            .evaluate_group(members, ctx.model_copy(update={"knowledge": k}))
            .compatible
        )


def test_thermal_group_requires_equal_source_durations_and_common_device():
    members, ctx = fixture_group(thermal=True)
    assert RuleEngine().evaluate_group(members, ctx).compatible
    rule = ctx.knowledge.rules[0]
    spec = GroupRuleSpec.model_validate_json(rule.group_compatibility_predicate)
    rule = rule.model_copy(
        update={
            "group_compatibility_predicate": spec.model_copy(
                update={"duration_sec": 900}
            ).model_dump_json()
        }
    )
    ctx = ctx.model_copy(update={"knowledge": ctx.knowledge.model_copy(update={"rules": (rule,)})})
    assert not RuleEngine().evaluate_group(members, ctx).compatible


def test_unstructured_or_unversioned_rule_is_not_executed():
    members, ctx = fixture_group()
    rule = ctx.knowledge.rules[0].model_copy(
        update={"group_compatibility_predicate": '__import__("os").system("never")'}
    )
    ctx = ctx.model_copy(update={"knowledge": ctx.knowledge.model_copy(update={"rules": (rule,)})})
    assert not RuleEngine().evaluate_group(members, ctx).compatible


def test_thermal_group_rejects_unknown_device_state():
    from app.domain.runtime_snapshot import DeviceState

    members, ctx = fixture_group(thermal=True)
    device = next(d for d in ctx.knowledge.devices if d.device_instance_id == "steam_oven_1")
    state = DeviceState(
        device_instance_id=device.device_instance_id,
        physical_resource_id=device.physical_resource_id,
        component_id=device.component_id,
        availability_status="UNKNOWN",
        occupancy_status="FREE",
        observed_at=ctx.runtime.time_origin.start_at,
        source="SIMULATED",
    )
    ctx = ctx.model_copy(
        update={"runtime": ctx.runtime.model_copy(update={"device_states": (state,)})}
    )
    assert not RuleEngine().evaluate_group(members, ctx).compatible


def test_delegated_review_artifacts_bind_real_sources_without_formal_approval():
    import json

    from tests.compiler_support import ROOT

    report = json.loads(
        (ROOT / "data/development/p3-delegated-review-v1.json").read_text(encoding="utf-8")
    )
    assert len(report["decisions"]) == 13
    assert report["actor_kind"] == "MODEL"
    assert report["formal_human_review_complete"] is False
    for thermal, index in ((False, 0), (True, 1)):
        members, ctx = fixture_group(thermal)
        rule = ProcessingRule.model_validate(report["processing_rules"][index])
        assert rule.review_status == "NEEDS_REVIEW"
        ctx = ctx.model_copy(
            update={"knowledge": ctx.knowledge.model_copy(update={"rules": (rule,)})}
        )
        assert RuleEngine().evaluate_group(members, ctx).compatible


def test_rejected_decision_keeps_rule_and_source_evidence():
    members, ctx = fixture_group()
    late = members[0].model_copy(update={"latest_end_sec": 1})
    decision = RuleEngine().evaluate_group((late, members[1]), ctx)
    assert not decision.compatible
    assert "test-group" in decision.rule_refs
    assert "test:explicit-synthetic-rule" in decision.evidence_refs
    precheck = RuleEngine().evaluate_group((members[0], members[0]), ctx)
    assert not precheck.compatible
    assert precheck.evidence_refs
