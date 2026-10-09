"""算法输入均为不可变纯数据；实际求解器索引属于 SolverBuildReport。"""

from __future__ import annotations

from typing import Literal, Self
from weakref import ReferenceType, ref

from pydantic import Field, model_validator

from app.domain.advance_preparation import AdvancePreparation
from app.domain.base import FrozenModel, NonEmpty, NonNegativeInt, PositiveInt, content_hash
from app.domain.canonical_recipe import OperationTemplate
from app.domain.cooking_completion import RecipeCookingCompletion
from app.domain.duration_estimate import DurationBuffer
from app.domain.ids import CarrierId, ExecutionId, OperationId, RecipeId, RecipeInstanceId, TaskId
from app.domain.inventory import InventoryFulfillment, InventorySupply
from app.domain.knowledge import EvidenceIndexEntry
from app.domain.material import MaterialRequirement
from app.domain.material_flow import MaterialAllocationModel, MaterialFlow
from app.domain.policy import ModelSize, SchedulingPolicy
from app.domain.processing_rules import ProcessingRule
from app.domain.pruning import PruningRecord
from app.domain.resources import DeviceInstance, DeviceProfile, ResourceUse
from app.domain.runtime_constraints import ResourceBlock
from app.domain.runtime_snapshot import ExecutionRecord, RuntimeSnapshot
from app.domain.thermal import ExpandedPrograms
from app.domain.transitions import BatchTransitionBinding

_PROBLEM_IDENTITIES: dict[int, tuple[ReferenceType[SchedulingProblem], str]] = {}


def _forget_problem(identity: int, reference: ReferenceType[SchedulingProblem]) -> None:
    entry = _PROBLEM_IDENTITIES.get(identity)
    if entry is not None and entry[0] is reference:
        _PROBLEM_IDENTITIES.pop(identity, None)


class RecipeInstance(FrozenModel):
    recipe_instance_id: RecipeInstanceId
    recipe_id: RecipeId
    name: NonEmpty


class LogicalTask(FrozenModel):
    task_id: TaskId
    recipe_instance_id: RecipeInstanceId
    operation_id: OperationId
    operation: OperationTemplate
    earliest_start_sec: NonNegativeInt = 0
    latest_end_sec: NonNegativeInt | None = None


class TaskDependency(FrozenModel):
    predecessor_id: TaskId
    successor_id: TaskId
    min_lag_sec: NonNegativeInt = 0
    max_lag_sec: NonNegativeInt | None = None
    evidence_refs: tuple[NonEmpty, ...] = ()


class MemberTimeOffset(FrozenModel):
    task_id: TaskId
    start_offset_sec: NonNegativeInt
    end_offset_sec: NonNegativeInt


class CarrierResourcePhase(FrozenModel):
    task_id: TaskId
    start_offset_sec: NonNegativeInt
    end_offset_sec: NonNegativeInt
    resource_use: ResourceUse


class CandidateCarrier(FrozenModel):
    carrier_id: CarrierId
    kind: Literal["STANDALONE", "SHARED_PREP", "THERMAL_BATCH", "INVENTORY_SUPPLY"]
    covers: tuple[TaskId, ...]
    duration_sec: NonNegativeInt
    resume_execution_id: ExecutionId | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    resource_uses: tuple[ResourceUse, ...] = ()
    rule_refs: tuple[NonEmpty, ...] = ()
    material_inputs: tuple[MaterialRequirement, ...] = ()
    material_outputs: tuple[MaterialRequirement, ...] = ()
    mandatory_recipe_batch: bool = False
    provenance_refs: tuple[NonEmpty, ...] = ()
    member_offsets: tuple[MemberTimeOffset, ...] = ()
    resource_phases: tuple[CarrierResourcePhase, ...] = ()
    replaced_reservation_ids: tuple[NonEmpty, ...] = ()
    transition_binding: BatchTransitionBinding | None = None
    inventory_supply: InventorySupply | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def phase_contract(self) -> Self:
        if self.kind == "INVENTORY_SUPPLY":
            if self.inventory_supply is None or self.duration_sec != 0 or self.resource_uses:
                raise ValueError("库存供应必须绑定实物资格，不得伪造加工占用")
        elif self.inventory_supply is not None:
            raise ValueError("加工载体不能携带库存供应证明")
        if self.kind == "THERMAL_BATCH":
            if len(self.member_offsets) != len(self.covers) or {
                p.task_id for p in self.member_offsets
            } != set(self.covers):
                raise ValueError("热批次必须完整映射每个被替代工序")
        elif (
            self.member_offsets
            or self.resource_phases
            or self.replaced_reservation_ids
            or self.transition_binding
        ):
            raise ValueError("只有显式热批次可以使用内部阶段映射")
        spans: tuple[MemberTimeOffset | CarrierResourcePhase, ...] = (
            *self.member_offsets,
            *self.resource_phases,
        )
        for span in spans:
            if (
                span.task_id not in self.covers
                or not 0 <= span.start_offset_sec <= span.end_offset_sec <= self.duration_sec
                or (
                    span.start_offset_sec == span.end_offset_sec
                    and not (
                        isinstance(span, MemberTimeOffset)
                        and self.transition_binding is not None
                        and span.task_id in self.transition_binding.preheat_task_ids
                    )
                )
            ):
                raise ValueError("载体内部阶段超出预约范围")
        return self


class ConstraintRecord(FrozenModel):
    constraint_id: NonEmpty
    category: NonEmpty
    task_ids: tuple[TaskId, ...] = ()
    carrier_ids: tuple[CarrierId, ...] = ()
    resource_ids: tuple[NonEmpty, ...] = ()
    rule_id: NonEmpty | None = None
    evidence_refs: tuple[NonEmpty, ...] = ()
    hardness: Literal["MANDATORY", "POLICY_BOUND", "OPTIMIZATION_BOUND"]
    origin: Literal["RECIPE", "DEVICE", "RUNTIME", "COMPILER", "OBJECTIVE_STAGE"]
    expression_summary: NonEmpty


class CandidateGenerationReport(FrozenModel):
    enumeration_complete: bool = True
    generated_count_is_exact: bool = True
    generated_count: NonNegativeInt = 0
    retained_count: NonNegativeInt = 0
    candidate_truncated: bool = False
    may_lose_optimum: bool = False
    removed_equivalent_ids: tuple[CarrierId, ...] = ()
    truncation_reasons: tuple[NonEmpty, ...] = ()


class SerialReference(FrozenModel):
    policy_version: NonEmpty
    duration_sec: NonNegativeInt
    validated_candidate_hash: NonEmpty


class SchedulingProblem(FrozenModel):
    schema_version: NonEmpty = "1.0"
    problem_id: NonEmpty
    knowledge_version: NonEmpty
    knowledge_release_kind: Literal["development", "sample", "competition"] = "sample"
    rule_version: NonEmpty
    snapshot_id: NonEmpty
    snapshot_schema_version: NonEmpty
    policy: SchedulingPolicy
    runtime: RuntimeSnapshot
    horizon_sec: PositiveInt
    recipe_instances: tuple[RecipeInstance, ...]
    logical_tasks: tuple[LogicalTask, ...] = ()
    cooking_completions: tuple[RecipeCookingCompletion, ...] = Field(
        default=(), exclude_if=lambda value: not value
    )
    dependencies: tuple[TaskDependency, ...] = ()
    resources: tuple[DeviceInstance, ...] = ()
    device_profiles: tuple[DeviceProfile, ...] = ()
    transition_rules: tuple[ProcessingRule, ...] = ()
    standalone_candidates: tuple[CandidateCarrier, ...] = ()
    shared_prep_candidates: tuple[CandidateCarrier, ...] = ()
    thermal_batch_candidates: tuple[CandidateCarrier, ...] = ()
    inventory_supply_candidates: tuple[CandidateCarrier, ...] = ()
    fixed_executions: tuple[ExecutionRecord, ...] = ()
    fixed_supply_fulfillments: tuple[InventoryFulfillment, ...] = Field(
        default=(), exclude_if=lambda value: not value
    )
    advance_preparations: tuple[AdvancePreparation, ...] = Field(
        default=(), exclude_if=lambda value: not value
    )
    inventory_rejections: tuple[NonEmpty, ...] = Field(
        default=(), exclude_if=lambda value: not value
    )
    material_supply_and_demand: tuple[MaterialRequirement, ...] = ()
    serial_reference: SerialReference | None = None
    candidate_generation_report: CandidateGenerationReport = CandidateGenerationReport()
    constraint_catalog: tuple[ConstraintRecord, ...] = ()
    model_size_estimate: ModelSize = ModelSize()
    model_estimated_proto_bytes: NonNegativeInt = 0
    model_soft_limit_exceedances: tuple[NonEmpty, ...] = ()
    pruning_records: tuple[PruningRecord, ...] = ()
    provenance_index: tuple[EvidenceIndexEntry, ...] = ()
    mandatory_programs: ExpandedPrograms = ExpandedPrograms()
    material_flow: MaterialFlow = MaterialFlow()
    material_allocations: MaterialAllocationModel = MaterialAllocationModel()
    resource_blocks: tuple[ResourceBlock, ...] = ()
    duration_buffers: tuple[DurationBuffer, ...] = Field(
        default=(), exclude_if=lambda value: not value
    )

    @model_validator(mode="after")
    def identity_consistency(self) -> Self:
        if (self.knowledge_version, self.rule_version, self.snapshot_id) != (
            self.runtime.knowledge_version,
            self.runtime.rule_version,
            self.runtime.snapshot_id,
        ):
            raise ValueError("问题与运行快照的知识身份不一致")
        instance_ids = {i.recipe_instance_id for i in self.recipe_instances}
        if len(instance_ids) != len(self.recipe_instances):
            raise ValueError("菜谱实例身份重复")
        tasks = {t.task_id for t in self.logical_tasks}
        if len(tasks) != len(self.logical_tasks):
            raise ValueError("需求身份重复")
        if any(t.recipe_instance_id not in instance_ids for t in self.logical_tasks):
            raise ValueError("需求引用不存在的菜谱实例")
        boundary_ids = [b.recipe_instance_id for b in self.cooking_completions]
        if len(set(boundary_ids)) != len(boundary_ids) or not set(boundary_ids) <= instance_ids:
            raise ValueError("出锅边界的菜谱实例身份无效或重复")
        owners = {t.task_id: t.recipe_instance_id for t in self.logical_tasks}
        if any(
            len(set(b.task_ids)) != len(b.task_ids)
            or any(owners.get(t) != b.recipe_instance_id for t in b.task_ids)
            for b in self.cooking_completions
        ):
            raise ValueError("出锅边界引用错误的工序")
        if (
            self.policy.objective.spread_basis == "COOKING_FINISH"
            and set(boundary_ids) != instance_ids
        ):
            raise ValueError("出锅目标缺少完整工艺锚点")
        cooking_tasks = {t for boundary in self.cooking_completions for t in boundary.task_ids}
        inventory_tasks = {
            t for carrier in self.inventory_supply_candidates for t in carrier.covers
        } | {t for supply in self.fixed_supply_fulfillments for t in supply.task_ids}
        inventory_tasks.update(t for item in self.advance_preparations for t in item.task_ids)
        if cooking_tasks & inventory_tasks:
            raise ValueError("库存替代覆盖出锅节点，但库存缺少可追溯的原出锅时刻")
        if any(
            d.predecessor_id not in tasks or d.successor_id not in tasks for d in self.dependencies
        ):
            raise ValueError("需求依赖悬空")
        candidates = (
            *self.standalone_candidates,
            *self.shared_prep_candidates,
            *self.thermal_batch_candidates,
            *self.inventory_supply_candidates,
        )
        if len({c.carrier_id for c in candidates}) != len(candidates):
            raise ValueError("候选身份重复")
        if any(not c.covers or any(t not in tasks for t in c.covers) for c in candidates):
            raise ValueError("候选覆盖需求为空或悬空")
        return self

    @property
    def fixed_task_fulfillments(self) -> tuple[InventoryFulfillment | AdvancePreparation, ...]:
        """已有供应和用户备料声明只满足逻辑前置，不生成执行事实。"""
        return (*self.fixed_supply_fulfillments, *self.advance_preparations)

    @property
    def problem_hash(self) -> str:
        # 缓存不进入模型、序列化或相等比较；Pydantic 再校验及 model_copy 都产生新身份。
        identity = id(self)
        cached = _PROBLEM_IDENTITIES.get(identity)
        if cached is not None and cached[0]() is self:
            return cached[1]
        digest = content_hash(self)
        reference = ref(self, lambda pointer: _forget_problem(identity, pointer))
        _PROBLEM_IDENTITIES[identity] = reference, digest
        return digest
