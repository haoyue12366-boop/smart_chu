"""显式设备状态、完整配置和表驱动转换；时间均为相对原点整数秒。"""

from typing import Literal, Self

from pydantic import model_validator

from app.domain.base import Digest, FrozenModel, NonEmpty, NonNegativeInt, content_hash
from app.domain.ids import TaskId
from app.domain.processing_rules import ProcessingRule
from app.domain.resources import ConfigurationValue

ThermalCondition = Literal["OFF", "COLD", "HOLDING", "UNKNOWN", "TRANSITION_COMPLETED"]


class ThermalProfile(FrozenModel):
    physical_resource_id: NonEmpty
    component_id: NonEmpty
    profile_id: NonEmpty
    configuration: tuple[ConfigurationValue, ...]

    @model_validator(mode="after")
    def unique_parameters(self) -> Self:
        if not self.configuration or len({c.parameter for c in self.configuration}) != len(
            self.configuration
        ):
            raise ValueError("转换配置必须非空且参数不重复")
        return self

    @property
    def profile_key(self) -> str:
        return content_hash(
            self.model_copy(
                update={
                    "configuration": tuple(sorted(self.configuration, key=lambda c: c.parameter))
                }
            )
        )


class ThermalState(FrozenModel):
    physical_resource_id: NonEmpty
    component_id: NonEmpty
    condition: ThermalCondition
    profile: ThermalProfile | None = None
    at_sec: NonNegativeInt
    valid_until_sec: NonNegativeInt | None = None
    origin: Literal["OBSERVED", "PLANNED"]
    source_ref: NonEmpty
    completed_rule_id: NonEmpty | None = None
    completed_rule_version: NonEmpty | None = None

    @model_validator(mode="after")
    def consistent_state(self) -> Self:
        if self.profile is not None and (self.physical_resource_id, self.component_id) != (
            self.profile.physical_resource_id,
            self.profile.component_id,
        ):
            raise ValueError("设备状态与配置的物理身份不同")
        if self.condition in {"HOLDING", "TRANSITION_COMPLETED"} and self.profile is None:
            raise ValueError("保持或已完成转换必须有明确配置")
        if self.valid_until_sec is not None and self.valid_until_sec < self.at_sec:
            raise ValueError("设备状态有效期早于观测")
        if self.condition == "TRANSITION_COMPLETED" and not (
            self.completed_rule_id and self.completed_rule_version
        ):
            raise ValueError("已发生转换必须绑定实际规则版本")
        return self


class TransitionHumanPhase(FrozenModel):
    start_offset_sec: NonNegativeInt
    end_offset_sec: NonNegativeInt

    @model_validator(mode="after")
    def positive_span(self) -> Self:
        if self.end_offset_sec <= self.start_offset_sec:
            raise ValueError("转换人工阶段必须为正时长")
        return self


class TransitionRuleSpec(FrozenModel):
    schema_version: Literal["thermal-transition-v1"] = "thermal-transition-v1"
    target_group_rule_id: NonEmpty | None = None
    target_group_rule_hash: Digest | None = None
    load_boundary: Literal["ISOLATED_UNTIL_HEAT"] | None = None
    scope_note: NonEmpty | None = None
    from_condition: Literal["OFF", "COLD", "HOLDING", "UNKNOWN"]
    from_profile: ThermalProfile | None = None
    to_profile: ThermalProfile
    max_idle_sec: NonNegativeInt
    human_phases: tuple[TransitionHumanPhase, ...] = ()
    authority: Literal["HUMAN_REVIEWED", "DELEGATED_DEVELOPMENT_ESTIMATE"]
    authorization_ref: NonEmpty

    @model_validator(mode="after")
    def bound_from_state(self) -> Self:
        if self.from_condition == "HOLDING" and self.from_profile is None:
            raise ValueError("保持状态转换表必须明确起始配置")
        if self.from_profile is not None and (
            self.from_profile.physical_resource_id,
            self.from_profile.component_id,
        ) != (self.to_profile.physical_resource_id, self.to_profile.component_id):
            raise ValueError("热状态转换不能跨物理设备或组件")
        return self


class TransitionTarget(FrozenModel):
    profile: ThermalProfile
    at_sec: NonNegativeInt
    rules: tuple[ProcessingRule, ...]
    release_kind: Literal["development", "sample", "competition"]
    allow_delegated_estimates: bool = False


class TransitionPlan(FrozenModel):
    allowed: bool
    kind: Literal[
        "COLD_START",
        "REUSE",
        "CONFIGURATION_CHANGE",
        "EXPLICIT_RESET",
        "ALREADY_COMPLETED",
        "REJECTED",
    ]
    duration_sec: NonNegativeInt | None
    human_phases: tuple[TransitionHumanPhase, ...] = ()
    rule_refs: tuple[NonEmpty, ...] = ()
    evidence_refs: tuple[NonEmpty, ...] = ()
    source_state_hash: Digest
    target_profile_key: Digest
    effective_at_sec: NonNegativeInt
    rejection_reason: NonEmpty | None = None


class BatchTransitionBinding(FrozenModel):
    completed_state_ref: NonEmpty | None = None
    rule_id: NonEmpty
    source_preheat_sec: NonNegativeInt
    transition_offset_sec: NonNegativeInt
    preheat_task_ids: tuple[TaskId, ...]
