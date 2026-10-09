from __future__ import annotations

from typing import Self

from pydantic import AwareDatetime, Field, model_validator

from app.domain.base import Digest, FrozenModel, NonEmpty, NonNegativeInt, PositiveInt, content_hash
from app.domain.ids import CarrierId, RecipeInstanceId, SessionId, TaskId
from app.domain.planning_timing import PlanningOverhead
from app.domain.resources import ResourceUse
from app.domain.time import Interval, TimeOrigin
from app.domain.validation_contract import ValidationReport


class ScheduledAssignment(FrozenModel):
    carrier_id: CarrierId
    task_ids: tuple[TaskId, ...]
    interval: Interval
    resource_uses: tuple[ResourceUse, ...] = ()


class RecipeCompletion(FrozenModel):
    recipe_instance_id: RecipeInstanceId
    completion_sec: NonNegativeInt


class RecipeCookingFinish(FrozenModel):
    recipe_instance_id: RecipeInstanceId
    cooking_finish_sec: NonNegativeInt


class ScheduleDisruption(FrozenModel):
    time_shift_sec: NonNegativeInt = 0
    resource_changes: NonNegativeInt = 0
    group_changes: NonNegativeInt = 0


class ScheduleMetrics(FrozenModel):
    makespan_sec: NonNegativeInt
    completion_spread_sec: NonNegativeInt
    cooking_finish_spread_sec: NonNegativeInt | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    remaining_cooking_finish_spread_sec: NonNegativeInt | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    recipe_cooking_finishes: tuple[RecipeCookingFinish, ...] = Field(
        default=(), exclude_if=lambda value: not value
    )
    max_continuous_human_sec: NonNegativeInt
    serial_reference_sec: NonNegativeInt | None = None
    total_human_work_sec: NonNegativeInt = 0
    actual_human_work_sec: NonNegativeInt = 0
    remaining_human_work_sec: NonNegativeInt = 0
    remaining_makespan_sec: NonNegativeInt = 0
    remaining_max_continuous_human_sec: NonNegativeInt = 0
    remaining_completion_spread_sec: NonNegativeInt = 0
    disruption: ScheduleDisruption | None = None


class CandidateSchedule(FrozenModel):
    problem_hash: Digest
    assignments: tuple[ScheduledAssignment, ...]
    recipe_completions: tuple[RecipeCompletion, ...] = ()
    metrics: ScheduleMetrics | None = None

    @property
    def candidate_hash(self) -> str:
        return content_hash(self)


class ValidatedSchedule(FrozenModel):
    candidate: CandidateSchedule
    validation: ValidationReport

    @model_validator(mode="after")
    def proof_matches(self) -> Self:
        if not self.validation.valid or self.validation.problem_hash != self.candidate.problem_hash:
            raise ValueError("校验未通过或问题身份不匹配")
        if self.validation.candidate_hash != self.candidate.candidate_hash:
            raise ValueError("校验结果不属于当前候选")
        return self


class PublishContext(FrozenModel):
    session_id: SessionId
    base_state_revision: NonNegativeInt
    base_plan_version: NonNegativeInt
    knowledge_version: NonEmpty
    snapshot_id: NonEmpty
    request_id: NonEmpty
    publication_id: NonEmpty


class PublishedPlan(FrozenModel):
    session_id: SessionId
    plan_version: PositiveInt
    parent_plan_version: NonNegativeInt
    state_revision: NonNegativeInt
    knowledge_version: NonEmpty
    snapshot_id: NonEmpty
    time_origin: TimeOrigin
    validated: ValidatedSchedule
    publication_id: NonEmpty
    committed_at: AwareDatetime
    planning_overhead: PlanningOverhead | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    serial_reference: ValidatedSchedule | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def version_order(self) -> Self:
        if self.plan_version <= self.parent_plan_version:
            raise ValueError("发布版本必须递增")
        if self.serial_reference is not None and (
            self.serial_reference.candidate.problem_hash != self.validated.candidate.problem_hash
        ):
            raise ValueError("串行参考与发布计划不属于同一问题")
        return self


class PublishConflict(FrozenModel):
    publication_id: NonEmpty
    reason: NonEmpty
    current_state_revision: NonNegativeInt
    current_plan_version: NonNegativeInt
