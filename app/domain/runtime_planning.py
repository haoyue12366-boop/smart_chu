"""内部事件与重排结果；P5 可在此之上适配 HTTP。"""

from typing import Literal

from pydantic import Field

from app.domain.base import FrozenModel, NonNegativeInt
from app.domain.events import EventApplyResult
from app.domain.reports import PlanningResult
from app.domain.schedule import PublishedPlan


class RuntimePlanningResult(FrozenModel):
    status: Literal["PUBLISHED", "NO_REPLAN", "PENDING", "FAILED", "EVENT_REJECTED"]
    event: EventApplyResult | None = None
    plan: PublishedPlan | None = None
    planning: PlanningResult | None = None
    attempts: NonNegativeInt = 0
    budget_ms: NonNegativeInt
    elapsed_ms: NonNegativeInt = 0
    replan_requested_sec: NonNegativeInt | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    replan_not_before_sec: NonNegativeInt | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
