from enum import StrEnum

from app.domain.base import FrozenModel, NonEmpty, ReviewStatus
from app.domain.ids import RecipeId


class IssueSeverity(StrEnum):
    BLOCKING = "BLOCKING"
    WARNING = "WARNING"


class DataIssue(FrozenModel):
    issue_id: NonEmpty
    recipe_id: RecipeId
    field_path: NonEmpty
    code: NonEmpty
    severity: IssueSeverity = IssueSeverity.BLOCKING
    description: NonEmpty
    evidence_refs: tuple[NonEmpty, ...]
    required_action: NonEmpty
    review_status: ReviewStatus = ReviewStatus.NEEDS_REVIEW
    resolution_review_ref: NonEmpty | None = None
