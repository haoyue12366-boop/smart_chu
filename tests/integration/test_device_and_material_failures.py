"""合成单工序配合发布设备事实，验证故障别名与未选备用资源的真实重排。"""

import pytest

from app.domain.canonical_recipe import CanonicalRecipeModel
from app.runtime.simulator import Simulator
from tests.integration.test_menu_events_replanning import service
from tests.runtime_support import event, p4_knowledge


def device_service(tmp_path, *, choice=True):
    knowledge = p4_knowledge()
    devices = {d.device_instance_id: d for d in knowledge.devices}
    aliases = tuple(
        devices[name].model_copy(update={"device_instance_id": "synthetic-alias-" + name})
        for name in ("burner_1", "burner_2")
    )
    published_use = next(
        use
        for recipe in knowledge.recipes
        for operation in recipe.operations
        for use in operation.resource_requirements
        if use.resource_id == "stove_choice"
    )
    recipe = CanonicalRecipeModel(
        schema_version="1.0",
        recipe_id="synthetic-device",
        recipe_version="1",
        name="合成灶眼故障工序",
        ingredient_requirements=(),
        material_specs=(),
        dependencies=(),
        operations=[
            {
                "operation_id": "cook",
                "action": "MIX",
                "duration": {"execution_sec": 60},
                "resource_requirements": [
                    {
                        **published_use.model_dump(),
                        "resource_id": "stove_choice" if choice else "burner_1",
                        "component_id": "selected_burner" if choice else "burner_1",
                    },
                    {
                        "resource_type": "HUMAN",
                        "resource_id": "human_1",
                        "conflict_policy": "UNARY",
                    },
                ],
            }
        ],
        provenance_refs=("synthetic:device-trigger",),
    )
    knowledge = knowledge.model_copy(
        update={
            "recipes": (recipe,),
            "recipe_contexts": (),
            "rules": (),
            "devices": (*knowledge.devices, *aliases),
        }
    )
    runtime, planning, session = service(tmp_path, knowledge=knowledge)
    first = planning.apply_event(
        event(
            session,
            "start",
            "START_SESSION",
            {"recipes": [{"id": recipe.recipe_id, "name": recipe.name}]},
        )
    )
    assert first.status == "PUBLISHED", first.model_dump_json()
    return runtime, planning


def selected_device(runtime):
    return runtime.get("flow").bindings[0].carrier.resource_uses[0].resource_id


def device_event(runtime, identity, kind, device_id, *, at=0, **payload):
    return event(
        runtime.get("flow"),
        identity,
        kind,
        {"device_id": device_id, **payload},
        at=at,
    )


def test_alias_fault_blocks_stale_dispatch_and_replans_on_other_burner(tmp_path):
    runtime, planning = device_service(tmp_path)
    selected = selected_device(runtime)
    fault = device_event(
        runtime,
        "alias-down",
        "DEVICE_UNAVAILABLE",
        "synthetic-alias-" + selected,
        reason="合成故障",
    )
    applied = runtime.apply_event(fault)
    assert applied.status == "APPLIED"
    assert applied.requires_replan
    assert applied.replan_reasons == ("DEVICE_UNAVAILABLE",)
    assert runtime.get("flow").dispatch_blocked
    replanned = planning.drain("flow")
    assert replanned.status == "PUBLISHED", replanned
    assert selected_device(runtime) != selected
    assert replanned.plan.plan_version == 2
    assert replanned.plan.validated.validation.valid
    assert runtime.apply_event(fault) == applied
    assert planning.drain("flow").status == "NO_REPLAN"
    assert runtime.get("flow").runtime.current_plan_version == 2


@pytest.mark.parametrize("alias", [False, True])
def test_unselected_alternative_recovery_reopens_failed_remaining_plan(tmp_path, alias):
    runtime, planning = device_service(tmp_path)
    old_selected = selected_device(runtime)
    alternate = "burner_2" if old_selected == "burner_1" else "burner_1"
    for name in (old_selected, alternate):
        result = runtime.apply_event(
            device_event(runtime, "down-" + name, "DEVICE_UNAVAILABLE", name, reason="合成故障")
        )
        assert result.status == "APPLIED"
    failed = planning.drain("flow")
    assert failed.status == "FAILED", failed
    assert runtime.get("flow").runtime.current_plan_version == 1
    recovered = runtime.apply_event(
        device_event(
            runtime,
            "alternative-up",
            "DEVICE_RECOVERED",
            "synthetic-alias-" + alternate if alias else alternate,
        )
    )
    assert recovered.requires_replan
    assert recovered.replan_reasons == ("DEVICE_RECOVERED",)
    outcome = planning.drain("flow")
    assert outcome.status == "PUBLISHED", outcome
    assert selected_device(runtime) == alternate
    assert not runtime.get("flow").dispatch_blocked


def test_changed_alternative_recovery_triggers_once_without_pending_failure(tmp_path):
    runtime, planning = device_service(tmp_path)
    selected = selected_device(runtime)
    alternate = "burner_2" if selected == "burner_1" else "burner_1"
    down = runtime.apply_event(
        device_event(runtime, "alternate-down", "DEVICE_UNAVAILABLE", alternate, reason="合成故障")
    )
    assert down.requires_replan
    assert planning.drain("flow").status == "PUBLISHED"
    up = runtime.apply_event(device_event(runtime, "alternate-up", "DEVICE_RECOVERED", alternate))
    assert up.requires_replan
    assert planning.drain("flow").status == "PUBLISHED"
    version = runtime.get("flow").runtime.current_plan_version
    unchanged = runtime.apply_event(
        device_event(
            runtime, "same-observation", "DEVICE_RECOVERED", "synthetic-alias-" + alternate, at=1
        )
    )
    assert unchanged.status == "APPLIED"
    assert not unchanged.requires_replan
    assert planning.drain("flow").status == "NO_REPLAN"
    assert runtime.get("flow").runtime.current_plan_version == version


def test_completed_device_use_does_not_trigger_another_plan(tmp_path):
    runtime, planning = device_service(tmp_path, choice=False)
    Simulator(runtime, "flow").advance(60)
    before = runtime.get("flow")
    assert before.runtime.executions[0].status == "COMPLETED"
    assert not before.requires_replan
    changed = runtime.apply_event(
        device_event(
            runtime, "finished-down", "DEVICE_UNAVAILABLE", "burner_1", at=60, reason="合成故障"
        )
    )
    assert changed.status == "APPLIED"
    assert not changed.requires_replan
    assert planning.drain("flow").status == "NO_REPLAN"
    assert runtime.get("flow").runtime.executions == before.runtime.executions


def test_unrelated_component_observation_is_recorded_without_replanning(tmp_path):
    runtime, planning = device_service(tmp_path, choice=False)
    observed = runtime.apply_event(
        device_event(runtime, "unrelated-down", "DEVICE_UNAVAILABLE", "burner_2", reason="合成故障")
    )
    assert observed.status == "APPLIED"
    assert not observed.requires_replan
    assert planning.drain("flow").status == "NO_REPLAN"
    assert runtime.get("flow").runtime.state_revision == 2


def test_running_alias_fault_and_recovery_preserve_unknown_result_and_occupation(tmp_path):
    runtime, planning = device_service(tmp_path, choice=False)
    current = runtime.get("flow")
    task = current.bindings[0].assignment.task_ids[0]
    assert (
        runtime.apply_event(
            event(
                current, "began", "OPERATION_STARTED", {"task_id": task, "execution_id": "running"}
            )
        ).status
        == "APPLIED"
    )
    down = planning.apply_event(
        device_event(
            runtime,
            "down",
            "DEVICE_UNAVAILABLE",
            "synthetic-alias-burner_1",
            at=10,
            reason="合成故障",
        )
    )
    assert down.event.requires_replan
    assert down.status == "FAILED"
    assert down.planning.failure.code == "STATE_INCOMPLETE"
    before = runtime.get("flow")
    assert before.runtime.executions[0].status == "RUNNING"
    assert before.runtime.executions[0].remaining_sec is None
    assert before.runtime.executions[0].interruption_event_refs
    up = planning.apply_event(device_event(runtime, "up", "DEVICE_RECOVERED", "burner_1", at=20))
    assert up.event.requires_replan
    assert up.status == "FAILED"
    current = runtime.get("flow")
    assert current.runtime.details.occupancies == before.runtime.details.occupancies
    assert current.runtime.executions == before.runtime.executions
    assert current.dispatch_blocked
    wrong_release = runtime.apply_event(
        device_event(
            runtime,
            "wrong-release",
            "DEVICE_RELEASE_CONFIRMED",
            "burner_1",
            at=21,
            execution_id="old-execution",
        )
    )
    assert wrong_release.status == "REJECTED"
    assert runtime.get("flow").runtime.details.occupancies == before.runtime.details.occupancies
    released = runtime.apply_event(
        device_event(
            runtime,
            "released",
            "DEVICE_RELEASE_CONFIRMED",
            "synthetic-alias-burner_1",
            at=22,
            execution_id="running",
        )
    )
    assert released.requires_replan
    assert planning.drain("flow").status == "FAILED"
    final = runtime.get("flow")
    assert final.runtime.executions[0].status == "RUNNING"
    assert final.runtime.executions[0].resource_spans[0].interval.end_sec == 22
    assert any(
        o.released_at is None and o.resource.resource_type == "HUMAN"
        for o in final.runtime.details.occupancies
    )
    assert not any(
        o.released_at is None and o.resource.resource_type == "DEVICE"
        for o in final.runtime.details.occupancies
    )


def test_first_unchanged_availability_observation_does_not_republish(tmp_path):
    runtime, planning = device_service(tmp_path)
    observed = runtime.apply_event(
        device_event(runtime, "already-available", "DEVICE_RECOVERED", selected_device(runtime))
    )
    assert observed.status == "APPLIED"
    assert not observed.requires_replan
    assert planning.drain("flow").status == "NO_REPLAN"


def test_cancelled_unstarted_device_binding_does_not_trigger_replanning(tmp_path):
    runtime, _ = device_service(tmp_path, choice=False)
    current = runtime.get("flow")
    assert (
        runtime.apply_event(
            event(
                current,
                "cancel",
                "CANCEL_RECIPE",
                {"recipe_instance_id": current.menu[0].recipe_instance_id},
            )
        ).status
        == "APPLIED"
    )
    observed = runtime.apply_event(
        device_event(runtime, "cancelled-down", "DEVICE_UNAVAILABLE", "burner_1", reason="合成故障")
    )
    assert observed.status == "APPLIED"
    assert not observed.requires_replan
    assert runtime.get("flow").replan_reasons == ("CANCEL_RECIPE",)
