from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from app.domain.base import Digest, FrozenModel, NonEmpty, NonNegativeInt, PositiveInt
from app.domain.errors import ErrorCode
from app.domain.objectives import ObjectiveStage
from app.domain.policy import ModelSize
from app.domain.schedule import CandidateSchedule
from app.domain.validation_contract import ValidationReport as ValidationReport


class SolveStatus(StrEnum):
    OPTIMAL = "OPTIMAL"
    FEASIBLE = "FEASIBLE"
    UNKNOWN = "UNKNOWN"
    INFEASIBLE = "INFEASIBLE"
    MODEL_INVALID = "MODEL_INVALID"


class PhaseTiming(FrozenModel):
    stage: NonEmpty
    elapsed_ms: NonNegativeInt


class SolverIndexMapping(FrozenModel):
    constraint_id: NonEmpty
    variable_ids: tuple[NonEmpty, ...] = ()
    proto_constraint_indices: tuple[NonNegativeInt, ...] = ()
    assumption_group_id: NonEmpty | None = None


class SolverBuildReport(FrozenModel):
    problem_hash: Digest
    solver_build_id: NonEmpty
    objective_stage: NonEmpty
    solver_version: NonEmpty
    actual_model_size: ModelSize
    selected_path_alternatives: NonNegativeInt = 0
    candidate_truncated: bool = False
    may_lose_optimum: bool = False
    candidate_enumeration_complete: bool = True
    generated_candidate_count: NonNegativeInt = 0
    pruning_counts: dict[str, NonNegativeInt] = {}
    constraint_mappings: tuple[SolverIndexMapping, ...] = ()
    serialized_proto_bytes: NonNegativeInt = 0
    build_time_ms: NonNegativeInt = 0
    logical_tasks: NonNegativeInt = 0
    fixed_executions: NonNegativeInt = 0
    constraints_by_type: dict[str, NonNegativeInt] = {}
    stage_parameters: ObjectiveStage | None = None
    solve_status: SolveStatus | None = None
    search_workers: PositiveInt | None = Field(default=None, exclude_if=lambda value: value is None)


class PlanningFailure(FrozenModel):
    code: ErrorCode
    failure_class: Literal[
        "INPUT_INVALID",
        "PRECHECK_CONFLICT",
        "INFEASIBLE_MODEL",
        "NO_SOLUTION_WITHIN_BUDGET",
        "MODEL_INVALID",
        "STALE_STATE",
        "STATE_INCOMPLETE",
    ]
    message: NonEmpty
    evidence_refs: tuple[NonEmpty, ...] = ()


class CompilationFailure(PlanningFailure):
    issue_refs: tuple[NonEmpty, ...] = ()


class SolveResult(FrozenModel):
    status: SolveStatus
    problem_hash: Digest
    candidate: CandidateSchedule | None = None
    objective_stage: NonEmpty = "FEASIBILITY"
    objective_value: NonNegativeInt | None = None
    best_bound: NonNegativeInt | None = None
    search_workers: PositiveInt | None = Field(default=None, exclude_if=lambda value: value is None)
    optimal_for_retained_candidates_only: bool = True
    build_report_ref: NonEmpty | None = None
    diagnostic_ref: NonEmpty | None = None
    diagnostic_message: NonEmpty | None = None
    timings: tuple[PhaseTiming, ...] = ()

    @model_validator(mode="after")
    def status_matches_candidate(self) -> Self:
        success = self.status in (SolveStatus.FEASIBLE, SolveStatus.OPTIMAL)
        if success != (self.candidate is not None):
            raise ValueError("成功状态必须有候选；失败状态不得伪造成功数据")
        if self.candidate is not None and self.candidate.problem_hash != self.problem_hash:
            raise ValueError("求解结果与候选的问题身份不一致")
        return self


class GreedyResult(FrozenModel):
    status: Literal["CANDIDATE_FOUND", "CONSTRUCTION_FAILED", "BUDGET_EXHAUSTED"]
    candidate: CandidateSchedule | None = None
    reason: NonEmpty | None = None
    timings: tuple[PhaseTiming, ...] = ()
    decision_trace: tuple[NonEmpty, ...] = ()
    completed_variants: NonNegativeInt = 0
    rollback_count: NonNegativeInt = 0

    @model_validator(mode="after")
    def candidate_status(self) -> Self:
        if (self.status == "CANDIDATE_FOUND") != (self.candidate is not None):
            raise ValueError("Greedy 状态与候选不一致")
        return self


class PlanningResult(FrozenModel):
    first_validated_candidate_ms: NonNegativeInt | None = None
    selected_candidate_source: Literal["GREEDY", "CP_SAT"] | None = None
    solver_fallback_used: bool = False
    status: Literal["VALIDATED", "FAILED"]
    candidate: CandidateSchedule | None = None
    validation: ValidationReport | None = None
    failure: PlanningFailure | None = None
    solve_result: SolveResult | None = None
    timings: tuple[PhaseTiming, ...] = ()
    stage_results: tuple[SolveResult, ...] = ()
    human_objective_optimized: bool = False
    total_human_objective_optimized: bool = Field(default=False, exclude_if=lambda v: not v)
    stability_objective_optimized: bool = False
    makespan_cap_sec: NonNegativeInt | None = None
    serial_reference_candidate: CandidateSchedule | None = None
    serial_reference_validation: ValidationReport | None = None
    rejected_candidates: tuple[ValidationReport, ...] = ()

    @model_validator(mode="after")
    def outcome(self) -> Self:
        if (self.serial_reference_candidate is None) != (self.serial_reference_validation is None):
            raise ValueError("串行参考必须携带独立验证证据")
        if (
            self.serial_reference_candidate is not None
            and self.serial_reference_validation is not None
        ):
            proof, reference = self.serial_reference_validation, self.serial_reference_candidate
            if (
                not proof.valid
                or proof.candidate_hash != reference.candidate_hash
                or proof.problem_hash != reference.problem_hash
                or (
                    self.candidate is not None
                    and self.candidate.problem_hash != reference.problem_hash
                )
            ):
                raise ValueError("串行参考身份或验证证据不一致")
        if self.status == "VALIDATED":
            if self.candidate is None or self.validation is None or self.failure is not None:
                raise ValueError("成功规划必须有已校验候选")
            if (
                not self.validation.valid
                or self.validation.problem_hash != self.candidate.problem_hash
                or self.validation.candidate_hash != self.candidate.candidate_hash
            ):
                raise ValueError("候选与校验结果不匹配")
        elif self.failure is None or self.candidate is not None:
            raise ValueError("失败必须说明原因且无完整成功候选")
        return self


class DiagnosticReport(FrozenModel):
    diagnostic_id: NonEmpty
    problem_hash: Digest
    snapshot_id: NonEmpty
    original_status: SolveStatus
    failure_stage: NonEmpty
    scope: Literal["BASE_MODEL", "POLICY_MODEL", "OBJECTIVE_STAGE"]
    conflict_constraint_ids: tuple[NonEmpty, ...]
    evidence_refs: tuple[NonEmpty, ...]
    explanation_status: Literal["PROVEN_CONFLICT", "SUSPECTED", "INCOMPLETE"]
    base_model_feasibility: Literal["FEASIBLE", "INFEASIBLE", "UNKNOWN"]
    core_irreducible_proven: bool = False
    minimality_proven: bool = False
    candidate_truncated: bool = False
    horizon_relaxed_in_diagnostic: bool = False
    suggested_review_actions: tuple[NonEmpty, ...] = ()
    elapsed_ms: NonNegativeInt
    diagnosis_complete: bool
    solver_build_id: NonEmpty | None = None
    diagnostic_build_id: NonEmpty | None = None
    assumption_mappings: tuple[SolverIndexMapping, ...] = ()
    shrink_checks: NonNegativeInt = 0
    search_workers: Literal[1] = 1
    uncontrolled_constraint_count: NonNegativeInt = 0


class VerificationCommand(FrozenModel):
    command: tuple[str, ...]
    exit_code: int
    elapsed_ms: NonNegativeInt
    collected_count: NonNegativeInt
    passed_count: NonNegativeInt
    failed_count: NonNegativeInt
    skipped_count: NonNegativeInt
    passed: bool
    output: str


class VerificationTaskStatus(FrozenModel):
    status: Literal["VERIFIED", "DEVELOPMENT_VERIFIED", "FAILED"]
    own_checks_passed: bool
    quality_checks_passed: bool
    dependencies_passed: bool


class VerificationEnvironment(FrozenModel):
    python: NonEmpty
    platform: NonEmpty
    processor: str
    cpu_count: NonNegativeInt | None
    mode: Literal["offline", "integration"]
    random_seed: int | None
    dependencies: dict[str, str]


class VerificationReport(FrozenModel):
    development_authorization_ref: NonEmpty | None = None
    deferred_formal_requirements: tuple[NonEmpty, ...] = ()
    development_dependency_overrides: dict[str, list[str]] = {}
    schema_version: NonEmpty
    phase: NonEmpty
    gate: str | None
    task: str | None
    status: Literal["VERIFIED", "DEVELOPMENT_VERIFIED", "FAILED"]
    started_at: AwareDatetime
    finished_at: AwareDatetime
    elapsed_ms: NonNegativeInt
    code_fingerprint: Digest
    commands: tuple[VerificationCommand, ...]
    errors: tuple[str, ...]
    tasks: dict[str, VerificationTaskStatus]
    artifact_hashes: dict[str, Digest]
    environment: VerificationEnvironment
