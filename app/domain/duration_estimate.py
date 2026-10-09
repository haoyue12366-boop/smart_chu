"""计划估计、允许等待的缓冲与实际执行分开；无样本时没有经验分位数。"""

from typing import Literal, Self

from pydantic import Field, model_validator

from app.domain.base import Digest, FrozenModel, NonEmpty, NonNegativeInt, PositiveInt
from app.domain.ids import RecipeId, RecipeInstanceId, TaskId
from app.domain.knowledge import MenuKnowledgeView
from app.domain.policy import SchedulingPolicy


class DurationEstimate(FrozenModel):
    operation_template_id: NonEmpty
    recipe_id: RecipeId
    operation_id: NonEmpty
    operation_hash: Digest
    phase_type: Literal["ESTIMATED_HUMAN", "ESTIMATED_PREHEAT", "FIXED_PROCESS"]
    quantity_range: NonEmpty
    device_profile: tuple[NonEmpty, ...] = ()
    nominal_sec: PositiveInt
    allowed_min_sec: NonNegativeInt | None = None
    allowed_max_sec: PositiveInt | None = None
    estimate_source: Literal[
        "DELEGATED_REVIEWED_ESTIMATE", "OBSERVED_COMPARABLE", "SYNTHETIC_EXPERIMENT"
    ]
    applicable_conditions: tuple[NonEmpty, ...]
    sample_count: NonNegativeInt = 0
    empirical_p50_sec: PositiveInt | None = None
    empirical_p90_sec: PositiveInt | None = None
    data_version: NonEmpty
    reviewer: NonEmpty
    review_evidence: tuple[NonEmpty, ...]

    @model_validator(mode="after")
    def checked_estimate(self) -> Self:
        if not self.review_evidence or not self.applicable_conditions:
            raise ValueError("时长估计必须记录审核依据与适用条件")
        if self.allowed_min_sec is not None and self.nominal_sec < self.allowed_min_sec:
            raise ValueError("名义时长低于工艺下界")
        if self.allowed_max_sec is not None and self.nominal_sec > self.allowed_max_sec:
            raise ValueError("名义时长超过工艺上界")
        if self.empirical_p50_sec is not None or self.empirical_p90_sec is not None:
            if self.sample_count < 30 or self.estimate_source != "OBSERVED_COMPARABLE":
                raise ValueError("没有至少30条适用观测样本，不能声称经验分位数")
        if self.empirical_p50_sec is not None and self.empirical_p90_sec is not None:
            if self.empirical_p50_sec > self.empirical_p90_sec:
                raise ValueError("经验分位数倒置")
        return self


class DurationRoot(FrozenModel):
    recipe_instance_id: RecipeInstanceId
    recipe_id: RecipeId
    task_id: TaskId
    operation_id: NonEmpty
    earliest_start_sec: NonNegativeInt
    instance_already_started: bool


class DurationProblemInputs(FrozenModel):
    knowledge: MenuKnowledgeView
    policy: SchedulingPolicy
    now_offset_sec: NonNegativeInt
    roots: tuple[DurationRoot, ...]


class DurationBuffer(FrozenModel):
    recipe_instance_id: RecipeInstanceId
    recipe_id: RecipeId
    root_task_ids: tuple[TaskId, ...]
    buffer_sec: PositiveInt
    base_start_sec: NonNegativeInt
    not_before_sec: NonNegativeInt
    estimate_refs: tuple[NonEmpty, ...]
    data_version: NonEmpty
    kind: Literal["BUFFER"] = "BUFFER"
    placement: Literal["BEFORE_RECIPE_ROOTS"] = "BEFORE_RECIPE_ROOTS"
    # 这段等待不保留人工/设备；后续实际阶段仍使用原约束预约资源。
    resource_reservations: tuple[NonEmpty, ...] = Field(default=(), max_length=0)


class DurationAdjustedInputs(FrozenModel):
    inputs: DurationProblemInputs
    estimates: tuple[DurationEstimate, ...]
    buffers: tuple[DurationBuffer, ...]
