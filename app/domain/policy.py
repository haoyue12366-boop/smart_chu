from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, StrictInt, model_validator

from app.domain.advance_preparation import AdvancePreparationRule
from app.domain.base import FrozenModel, NonEmpty, NonNegativeInt, PositiveInt


class BudgetSpec(FrozenModel):
    total_ms: PositiveInt
    greedy_ms: PositiveInt
    solver_ms: PositiveInt
    publication_reserve_ms: PositiveInt

    @model_validator(mode="after")
    def fits(self) -> Self:
        if self.greedy_ms + self.solver_ms + self.publication_reserve_ms > self.total_ms:
            raise ValueError("阶段预算超出总预算")
        return self


class ModelSize(FrozenModel):
    total_variables: NonNegativeInt = 0
    boolean_variables: NonNegativeInt = 0
    optional_intervals: NonNegativeInt = 0
    total_constraints: NonNegativeInt = 0
    sequence_arcs: NonNegativeInt = 0


class ObjectiveSpec(FrozenModel):
    spread_basis: Literal["WORKFLOW_FINISH", "COOKING_FINISH"] = Field(
        default="WORKFLOW_FINISH", exclude_if=lambda value: value == "WORKFLOW_FINISH"
    )
    stages: tuple[
        Literal["MAKESPAN", "SPREAD", "TOTAL_HUMAN_WORK", "HUMAN_BUSY", "STABILITY"], ...
    ] = (
        "MAKESPAN",
        "SPREAD",
        "HUMAN_BUSY",
        "STABILITY",
    )
    spread_target_sec: NonNegativeInt = 240
    rest_gap_sec: PositiveInt = 60
    makespan_extra_basis_points: NonNegativeInt = 500
    makespan_extra_cap_sec: NonNegativeInt = 120


class GreedySearchLimits(FrozenModel):
    max_variants: PositiveInt = 3
    max_placements_per_iteration: PositiveInt = 24
    max_gap_checks_per_candidate: PositiveInt = 32
    rollback_depth: NonNegativeInt = 3
    rollback_attempts: NonNegativeInt = 8
    max_standalone_restarts: NonNegativeInt = 1


class SchedulingPolicy(FrozenModel):
    policy_version: NonEmpty
    advance_preparation_mode: Literal["SCHEDULE_ALL", "ASSUME_READY"] = Field(
        default="SCHEDULE_ALL", exclude_if=lambda value: value == "SCHEDULE_ALL"
    )
    advance_preparation_rules: tuple[AdvancePreparationRule, ...] = Field(
        default=(), exclude_if=lambda value: not value
    )
    time_grid_sec: PositiveInt = 1
    minute_output_mode: Literal["DECIMAL", "INTEGER"] = "DECIMAL"
    human_count: StrictInt = Field(default=1, ge=1, le=1)
    human_resource_id: Literal["human_1"] = "human_1"
    burner_ids: tuple[Literal["burner_1", "burner_2"], ...] = ("burner_1", "burner_2")
    max_nonstandalone_per_requirement: PositiveInt = 12
    max_nonstandalone_per_problem: PositiveInt = 256
    preserve_mandatory_recipe_batches: Literal[True] = True
    preserve_all_standalone_candidates: Literal[True] = True
    shared_prep: bool = False
    allow_delegated_shared_estimates: bool = False
    strict_together_batch: bool = False
    generic_common_start_batch: Literal[False] = False
    generic_common_finish_batch: Literal[False] = False
    enable_certified_non_equivalent_pruning: bool = False
    # 默认值不进入历史 JSON，既有策略身份保持；关闭开关用于 P6 受控实验。
    equivalence_deduplication: bool = Field(default=True, exclude_if=lambda value: value is True)
    graph_bound_preprocessing: bool = Field(default=True, exclude_if=lambda value: value is True)
    duration_policy_id: Literal["NOMINAL", "BUFFERED"] = "NOMINAL"
    critical_window_policy_id: Literal["NONE", "SERIAL_RECIPE_V1"] = Field(
        default="NONE", exclude_if=lambda value: value == "NONE"
    )
    replan_search_mode: Literal["OPTIMIZE", "FEASIBILITY_FIRST"] = Field(
        default="OPTIMIZE", exclude_if=lambda value: value == "OPTIMIZE"
    )
    # 执行派发的风险估计；不修改原工艺时长、资源容量或实际占用事实。
    dispatch_guard_policy_id: Literal["NONE", "TIGHT_HUMAN_V1"] = Field(
        default="NONE", exclude_if=lambda value: value == "NONE"
    )
    duration_data_version: NonEmpty = "nominal-v1"
    duration_buffer_basis_points: NonNegativeInt = Field(
        default=1500, le=5000, exclude_if=lambda value: value == 1500
    )
    initial_budget: BudgetSpec = BudgetSpec(
        total_ms=4200, greedy_ms=150, solver_ms=3000, publication_reserve_ms=400
    )
    replan_budget: BudgetSpec = BudgetSpec(
        total_ms=2400, greedy_ms=100, solver_ms=1500, publication_reserve_ms=300
    )
    objective: ObjectiveSpec = ObjectiveSpec()
    greedy_search: GreedySearchLimits = GreedySearchLimits()
    max_exact_human_phases: PositiveInt = 120
    minimum_exact_human_budget_ms: PositiveInt = 100
    model_soft_limits: ModelSize = ModelSize(
        total_variables=6000,
        boolean_variables=4000,
        optional_intervals=1500,
        total_constraints=20000,
        sequence_arcs=8000,
    )
    api_instances: Literal[1] = 1
    concurrent_planning_jobs: Literal[1] = 1
    solver_worker_processes: Literal[1] = 1
    max_solver_search_workers: PositiveInt = Field(default=4, le=4)
    sqlite_busy_timeout_ms: PositiveInt = 100
    sqlite_max_write_retries: NonNegativeInt = 2
    diagnostic_total_budget_ms: PositiveInt = 2000
    diagnostic_search_workers: Literal[1] = 1
    max_core_shrink_checks: NonNegativeInt = 6

    @property
    def quality_first(self) -> bool:
        """明确质量优先的联合目标；历史总人工策略保持原语义。"""
        return self.objective.stages in {
            ("SPREAD", "TOTAL_HUMAN_WORK", "MAKESPAN"),
            ("SPREAD", "HUMAN_BUSY", "MAKESPAN"),
        }

    @model_validator(mode="after")
    def burners(self) -> Self:
        if self.burner_ids != ("burner_1", "burner_2"):
            raise ValueError("灶眼固定为 burner_1、burner_2")
        return self
