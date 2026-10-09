from enum import StrEnum

from app.domain.base import FrozenModel, NonEmpty, NonNegativeInt, ReviewStatus
from app.domain.resources import ConfigurationValue


class RuleKind(StrEnum):
    SHARED_PREP = "SHARED_PREP"
    STRICT_TOGETHER = "STRICT_TOGETHER"
    FIXED_RECIPE = "FIXED_RECIPE"
    TRANSITION = "TRANSITION"
    INVENTORY_SUBSTITUTION = "INVENTORY_SUBSTITUTION"
    RECOVERY = "RECOVERY"


class ProcessingRule(FrozenModel):
    rule_id: NonEmpty
    rule_version: NonEmpty
    kind: RuleKind
    input_spec_ids: tuple[NonEmpty, ...] = ()
    output_spec_ids: tuple[NonEmpty, ...] = ()
    profile_ids: tuple[NonEmpty, ...] = ()
    required_configuration: tuple[ConfigurationValue, ...] = ()
    group_compatibility_predicate: NonEmpty
    transition_duration_sec: NonNegativeInt | None = None
    evidence_refs: tuple[NonEmpty, ...]
    review_status: ReviewStatus = ReviewStatus.DRAFT
