import json

import pytest
from pydantic import ValidationError

from app.domain.canonical_recipe import CanonicalRecipeModel
from app.domain.material import MaterialSpec
from app.domain.resources import DeviceInstance
from tests.helpers import recipe, recipe_payload


def test_roundtrip_preserves_parallel_batches_and_intervention():
    value = recipe()
    restored = CanonicalRecipeModel.model_validate_json(value.model_dump_json())
    assert restored == value
    assert len(restored.dependencies) == 3
    assert restored.operations[2].execution_policy.fixed_batch_id != (
        restored.operations[4].execution_policy.fixed_batch_id
    )
    assert restored.operations[2].execution_policy.interventions[0].operation_id.root == "add"
    assert CanonicalRecipeModel.model_json_schema()["properties"]["operations"]


def test_draft_unknown_duration_is_not_zero():
    payload = recipe_payload()
    payload["operations"][0]["duration"] = {}
    value = CanonicalRecipeModel.model_validate(payload)
    assert value.operations[0].duration.execution_sec is None
    payload["review_status"] = "APPROVED"
    with pytest.raises(ValidationError, match="时长|审核"):
        CanonicalRecipeModel.model_validate(payload)


@pytest.mark.parametrize("mutation", ["reference", "enum", "intervention", "zero", "mapping"])
def test_invalid_paths(mutation):
    payload = recipe_payload()
    if mutation == "reference":
        payload["dependencies"][0]["successor_id"] = "absent"
    elif mutation == "enum":
        payload["operations"][0]["action"] = "UNDEFINED"
    elif mutation == "intervention":
        payload["operations"][2]["execution_policy"]["interventions"][0]["operation_id"] = "absent"
    elif mutation == "zero":
        payload["operations"][0]["duration"]["execution_sec"] = 0
    else:
        payload["operations"][0]["resource_requirements"] = [
            {"resource_type": "DEVICE", "resource_id": "oven_1", "review_status": "APPROVED"}
        ]
    with pytest.raises(ValidationError):
        CanonicalRecipeModel.model_validate(payload)


def test_material_identity_and_device_aliases():
    a = MaterialSpec(
        spec_id="carrot_dice",
        ingredient_id="carrot",
        name="胡萝卜",
        state="raw",
        shape="dice",
        size_mm=5,
    )
    b = a.model_copy(update={"spec_id": "carrot_slice", "shape": "slice"})
    assert a != b
    steam = DeviceInstance(
        device_instance_id="steam",
        physical_resource_id="combi_1",
        component_id="chamber",
        capability_refs=("steam",),
        conflict_policy="BATCH_EXCLUSIVE",
    )
    bake = DeviceInstance(
        device_instance_id="bake",
        physical_resource_id="combi_1",
        component_id="chamber",
        capability_refs=("bake",),
        conflict_policy="BATCH_EXCLUSIVE",
    )
    assert steam.competition_key == bake.competition_key
    assert json.loads(steam.model_dump_json())["device_instance_id"] != "combi_1"


def test_exported_canonical_schema_is_current():
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "data/schemas/canonical_recipe.schema.json"
    assert json.loads(path.read_text(encoding="utf-8")) == CanonicalRecipeModel.model_json_schema()
