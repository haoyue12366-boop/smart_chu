import hashlib
import json
from typing import Literal

from app.domain.base import Digest, FrozenModel, NonEmpty, PositiveInt
from app.domain.canonical_recipe import CanonicalRecipeModel
from app.domain.ids import RecipeId
from app.domain.processing_rules import ProcessingRule
from app.domain.recipe_context import RecipeSchedulingContext
from app.domain.resources import DeviceInstance, DeviceProfile
from app.domain.validation_contract import ValidationViolation


class DeviceChoice(FrozenModel):
    choice_id: NonEmpty
    device_ids: tuple[NonEmpty, ...]


class ReleaseScope(FrozenModel):
    release_kind: Literal["development", "sample", "competition"]
    rule_version: NonEmpty
    time_grid_sec: PositiveInt = 1
    devices: tuple[DeviceInstance, ...] = ()
    device_choices: tuple[DeviceChoice, ...] = ()
    evidence_ids: tuple[NonEmpty, ...] = ()
    expected_recipe_ids: tuple[RecipeId, ...] = ()
    required_recipes: tuple[CanonicalRecipeModel, ...] = ()
    recipe_contexts: tuple[RecipeSchedulingContext, ...] = ()


class KnowledgeValidationReport(FrozenModel):
    input_hash: Digest
    validator_version: NonEmpty
    release_kind: Literal["development", "sample", "competition"]
    valid: bool
    formal_release_eligible: bool
    usable_recipe_ids: tuple[RecipeId, ...]
    violations: tuple[ValidationViolation, ...]
    single_recipe_solve_status: Literal["NOT_RUN"] = "NOT_RUN"


def knowledge_input_hash(
    recipes: tuple[CanonicalRecipeModel, ...],
    profiles: tuple[DeviceProfile, ...],
    rules: tuple[ProcessingRule, ...],
    scope: ReleaseScope,
) -> str:
    payload = {
        "recipes": [r.model_dump(mode="json") for r in recipes],
        "profiles": [p.model_dump(mode="json") for p in profiles],
        "rules": [r.model_dump(mode="json") for r in rules],
        "scope": scope.model_dump(mode="json"),
    }
    return hashlib.sha256(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


class ReleaseRef(FrozenModel):
    release_id: NonEmpty
    knowledge_version: NonEmpty
    rule_version: NonEmpty
    snapshot_id: NonEmpty
    manifest_hash: Digest
    release_kind: str


class SnapshotHandle(FrozenModel):
    release: ReleaseRef
    snapshot_schema_version: NonEmpty
    snapshot_hash: Digest
    recipe_count: PositiveInt


class EvidenceIndexEntry(FrozenModel):
    evidence_id: NonEmpty
    artifact_path: NonEmpty
    artifact_hash: Digest
    locator: NonEmpty


class MenuKnowledgeView(FrozenModel):
    release: ReleaseRef
    snapshot_schema_version: NonEmpty
    snapshot_hash: Digest
    recipes: tuple[CanonicalRecipeModel, ...]
    devices: tuple[DeviceInstance, ...]
    profiles: tuple[DeviceProfile, ...]
    rules: tuple[ProcessingRule, ...]
    provenance_index: tuple[EvidenceIndexEntry, ...]
    device_choices: tuple[DeviceChoice, ...] = ()
    recipe_contexts: tuple[RecipeSchedulingContext, ...] = ()
