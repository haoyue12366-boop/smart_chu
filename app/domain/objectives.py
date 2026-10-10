"""每轮优化附加界独立于原始不可变 SchedulingProblem。"""

from typing import Literal, Self

from pydantic import Field, model_validator

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
    quality_upper_bound: NonNegativeInt | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    thermal_seed: CandidateSchedule | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def quality_bound_stage(self) -> Self:
        if self.quality_upper_bound is not None and self.name != "E_QUALITY":
            raise ValueError("联合质量上界仅属于E_QUALITY阶段")
        if self.thermal_seed is not None and self.name != "D_HUMAN":
            raise ValueError("固定热工序的局部搜索仅属于D_HUMAN阶段")
        return self
