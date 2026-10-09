"""工艺发布门的合成反例；批准只用于这些明确 synthetic 的测试。"""

from datetime import UTC, datetime

import pytest

from app.domain.canonical_recipe import CanonicalRecipeModel
from app.domain.knowledge import ReleaseScope
from app.domain.resources import DeviceInstance, DeviceProfile
from app.validation.knowledge import validate_knowledge


def fixture(*, seconds=600, device_type="oven", temperature=180):
    profile = DeviceProfile(
        profile_id="synthetic-profile",
        device_type=device_type,
        mode="test-mode",
        constraints=(
            {
                "parameter": "temperature_c",
                "minimum": -20 if device_type == "fridge" else 60,
                "maximum": 10 if device_type == "fridge" else 230,
            },
            *(
                ()
                if device_type == "fridge"
                else ({"parameter": "duration_sec", "minimum": 1, "maximum": 10800},)
            ),
        ),
        rule_version="test-rules",
        provenance_refs=("synthetic-source",),
        review_status="APPROVED",
    )
    device = DeviceInstance(
        device_instance_id="device",
        physical_resource_id="physical",
        component_id="chamber",
        capability_refs=(profile.profile_id,),
        conflict_policy="UNARY",
        rule_version="test-rules",
        evidence_refs=("synthetic-source",),
        review_status="APPROVED",
    )
    draft = CanonicalRecipeModel.model_validate(
        {
            "schema_version": "1.0",
            "recipe_id": "synthetic",
            "name": "合成知识校验",
            "recipe_version": "test-v1",
            "ingredient_requirements": [],
            "material_specs": [],
            "provenance_refs": ["synthetic-source"],
            "dependencies": [],
            "operations": [
                {
                    "operation_id": "heat",
                    "action": "CHILL" if device_type == "fridge" else "HEAT",
                    "duration": {
                        "execution_sec": seconds,
                        "nominal_sec": seconds,
                        "fixed_process_time": True,
                        "source_ref": "synthetic-source",
                    },
                    "provenance_refs": ["synthetic-source"],
                    "resource_requirements": [
                        {
                            "resource_type": "DEVICE",
                            "resource_id": "device",
                            "physical_resource_id": "physical",
                            "component_id": "chamber",
                            "conflict_policy": "UNARY",
                            "profile_options": ["synthetic-profile"],
                            "configuration": [
                                {"parameter": "mode", "value": "test-mode"},
                                {"parameter": "temperature_c", "value": temperature},
                            ],
                            "rule_version": "test-rules",
                            "evidence_refs": ["synthetic-source"],
                            "review_status": "APPROVED",
                        }
                    ],
                }
            ],
        }
    )
    return draft, (profile,), device


def scope(device, **changes):
    return ReleaseScope(
        release_kind="development",
        rule_version="test-rules",
        devices=(device,),
        evidence_ids=("synthetic-source",),
        **changes,
    )


def altered(recipe, edit):
    payload = recipe.model_dump(mode="json")
    edit(payload)
    return CanonicalRecipeModel.model_validate(payload)


def codes(report):
    return {v.code for v in report.violations}


def test_development_accepts_structural_path_but_never_confers_formal_release():
    recipe, profiles, device = fixture()
    report = validate_knowledge((recipe,), profiles, (), scope(device))
    assert report.valid and report.usable_recipe_ids == (recipe.recipe_id,)
    assert report.formal_release_eligible is False
    assert report.single_recipe_solve_status == "NOT_RUN"
    formal = scope(device).model_copy(update={"release_kind": "sample"})
    assert "REVIEW_REQUIRED" in codes(validate_knowledge((recipe,), profiles, (), formal))


def test_sample_requires_matching_review_and_competition_exact_source_ids():
    recipe, profiles, device = fixture()
    payload = recipe.model_dump(mode="json")
    payload.update(
        review_status="APPROVED",
        approval={
            "review_id": "synthetic-review",
            "reviewer": "synthetic-tester",
            "reviewed_at": datetime.now(UTC).isoformat(),
            "evidence_refs": ["synthetic-source"],
            "approved_content_hash": recipe.semantic_hash(),
        },
    )
    approved = CanonicalRecipeModel.model_validate(payload)
    sample_scope = scope(device).model_copy(update={"release_kind": "sample"})
    assert validate_knowledge((approved,), profiles, (), sample_scope).valid
    competition = sample_scope.model_copy(update={"release_kind": "competition"})
    assert "COMPETITION_ID_COVERAGE" in codes(
        validate_knowledge((approved,), profiles, (), competition)
    )


@pytest.mark.parametrize(
    "mutation,code",
    [
        (
            lambda p: p["operations"][0]["resource_requirements"][0]["configuration"][1].update(
                value=240
            ),
            "DEVICE_PATH_INVALID",
        ),
        (
            lambda p: p["operations"][0]["resource_requirements"][0].update(rule_version="other"),
            "DEVICE_PATH_INVALID",
        ),
        (lambda p: p["operations"][0]["duration"].update(execution_sec=None), "MISSING_DURATION"),
        (lambda p: p["operations"][0].update(action="UNKNOWN"), "UNKNOWN_ACTION"),
        (lambda p: p.update(provenance_refs=["missing"]), "MISSING_EVIDENCE"),
    ],
)
def test_invalid_paths_fail_without_repairing_source(mutation, code):
    recipe, profiles, device = fixture()
    bad = altered(recipe, mutation)
    before = bad.document_hash
    assert code in codes(validate_knowledge((bad,), profiles, (), scope(device)))
    assert bad.document_hash == before


def test_long_cold_wait_not_limited_by_oven_capability():
    recipe, profiles, device = fixture(seconds=86400, device_type="fridge", temperature=4)
    assert validate_knowledge((recipe,), profiles, (), scope(device)).valid


def test_exact_heat_cannot_be_rounded_to_minute_and_lag_window_cannot_collapse():
    recipe, profiles, device = fixture(seconds=15)
    assert "TIME_GRID_INCOMPATIBLE" in codes(
        validate_knowledge((recipe,), profiles, (), scope(device, time_grid_sec=60))
    )
    assert validate_knowledge((recipe,), profiles, (), scope(device, time_grid_sec=1)).valid


def test_cycles_and_missing_required_intervention_rejected():
    recipe, profiles, device = fixture()
    p = recipe.model_dump(mode="json")
    p["operations"].append(
        {
            "operation_id": "add",
            "action": "ADD",
            "duration": {"execution_sec": 60, "source_ref": "synthetic-source"},
            "required": False,
            "provenance_refs": ["synthetic-source"],
        }
    )
    p["operations"][0]["execution_policy"]["interventions"] = [
        {"operation_id": "add", "offset_min_sec": 300, "offset_max_sec": 300}
    ]
    for before, after in (("heat", "add"), ("add", "heat")):
        p["dependencies"].append(
            {
                "predecessor_id": before,
                "successor_id": after,
                "reason": "合成环",
                "evidence_refs": ["synthetic-source"],
            }
        )
    bad = CanonicalRecipeModel.model_validate(p)
    assert {"PROCESS_CYCLE", "INVALID_INTERVENTION"} <= codes(
        validate_knowledge((bad,), profiles, (), scope(device))
    )


def test_material_without_producer_or_with_overconsumption_rejected():
    recipe, profiles, device = fixture()
    p = recipe.model_dump(mode="json")
    p["material_specs"] = [
        {
            "spec_id": "raw",
            "ingredient_id": "raw",
            "name": "合成原料",
            "state": "raw",
            "provenance_refs": ["synthetic-source"],
        }
    ]
    demand = {
        "requirement_id": "demand",
        "spec_id": "raw",
        "quantity_kind": "EXACT",
        "quantity": {"value": 2, "scale": 1, "unit": "g"},
        "provenance_refs": ["synthetic-source"],
    }
    p["operations"][0]["material_inputs"] = [demand]
    assert "MATERIAL_SOURCE_MISSING" in codes(
        validate_knowledge((CanonicalRecipeModel.model_validate(p),), profiles, (), scope(device))
    )
    p["ingredient_requirements"] = [{**demand, "quantity": {"value": 1, "scale": 1, "unit": "g"}}]
    assert "MATERIAL_OVERCONSUMED" in codes(
        validate_knowledge((CanonicalRecipeModel.model_validate(p),), profiles, (), scope(device))
    )


def test_unmapped_device_and_missing_heat_resource_are_blocked():
    recipe, profiles, device = fixture()
    bad = altered(recipe, lambda p: p["operations"][0].update(resource_requirements=[]))
    assert "DEVICE_REQUIRED" in codes(validate_knowledge((bad,), profiles, (), scope(device)))
    unmapped = altered(
        recipe,
        lambda p: p["operations"][0]["resource_requirements"][0].update(
            physical_resource_id=None, review_status="NEEDS_REVIEW"
        ),
    )
    assert "DEVICE_PATH_INVALID" in codes(
        validate_knowledge((unmapped,), profiles, (), scope(device))
    )


def test_source_fixed_process_cannot_disappear_even_if_remaining_graph_is_valid():
    recipe, profiles, device = fixture()
    p = recipe.model_dump(mode="json")
    p["operations"][0]["execution_policy"].update(
        batch_policy="FIXED_RECIPE", fixed_batch_id="batch"
    )
    baseline = CanonicalRecipeModel.model_validate(p)
    report = validate_knowledge(
        (recipe,), profiles, (), scope(device, required_recipes=(baseline,))
    )
    assert "MANDATORY_PROCESS_LOST" in codes(report)


def test_device_aliases_cannot_disagree_about_one_physical_resource_policy():
    recipe, profiles, device = fixture()
    alias = device.model_copy(
        update={"device_instance_id": "alias", "conflict_policy": "STATE_COMPATIBLE"}
    )
    context = scope(device).model_copy(update={"devices": (device, alias)})
    assert "PHYSICAL_POLICY_CONFLICT" in codes(validate_knowledge((recipe,), profiles, (), context))


@pytest.mark.parametrize("device_type", ["dishwasher", "coffee", "defrost"])
def test_all_used_device_classes_require_known_program_duration_and_mapping(device_type):
    recipe, profiles, device = fixture(device_type=device_type)
    assert validate_knowledge((recipe,), profiles, (), scope(device)).valid
    bad = altered(recipe, lambda p: p["operations"][0]["duration"].update(execution_sec=None))
    assert "MISSING_DURATION" in codes(validate_knowledge((bad,), profiles, (), scope(device)))


def test_minute_grid_detects_empty_lag_window_without_altering_exact_heat():
    recipe, profiles, device = fixture()
    p = recipe.model_dump(mode="json")
    p["operations"].append(
        {
            "operation_id": "serve",
            "action": "FINISH",
            "duration": {"execution_sec": 60, "source_ref": "synthetic-source"},
            "provenance_refs": ["synthetic-source"],
        }
    )
    p["dependencies"].append(
        {
            "predecessor_id": "heat",
            "successor_id": "serve",
            "min_lag_sec": 10,
            "max_lag_sec": 20,
            "reason": "合成紧窗口",
            "evidence_refs": ["synthetic-source"],
        }
    )
    bad = CanonicalRecipeModel.model_validate(p)
    assert "TIME_GRID_INCOMPATIBLE" in codes(
        validate_knowledge((bad,), profiles, (), scope(device, time_grid_sec=60))
    )
