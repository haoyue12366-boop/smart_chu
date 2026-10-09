"""每轮优化附加界独立于原始不可变 SchedulingProblem。"""

from typing import Literal

from pydantic import Field

from app.domain.base import FrozenModel, NonNegativeInt
from app.domain.schedule import CandidateSchedule


class ObjectiveStage(FrozenModel):
    name: Literal[
        "A_MAKESPAN",
        "B_SPREAD",
        "C_MAKESPAN",
        "D_TOTAL_HUMAN",
        "D_HUMAN",
        "D_STABILITY",
        "E_QUALITY",
    ]
    makespan_cap_sec: NonNegativeInt | None = None
    spread_excess_cap_sec: NonNegativeInt | None = None
    human_busy_cap_sec: NonNegativeInt | None = None
    total_human_cap_sec: NonNegativeInt | None = Field(default=None, exclude_if=lambda v: v is None)
    previous_plan: CandidateSchedule | None = None
