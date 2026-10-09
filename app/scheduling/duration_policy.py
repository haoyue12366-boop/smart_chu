"""可选的菜谱根节点显式等待缓冲；不改变热时长、人工工时或运行事实。"""

from collections import defaultdict
from typing import Literal

from app.domain.base import content_hash
from app.domain.canonical_recipe import OperationTemplate
from app.domain.duration_estimate import (
    DurationAdjustedInputs,
    DurationBuffer,
    DurationEstimate,
    DurationProblemInputs,
)
from app.domain.time import align_up

ESTIMATED_ACTIONS = frozenset({"CUT", "MIX", "CLEAN", "LOAD", "UNLOAD", "TRANSFER", "PREPARE"})


def estimated_phase(
    operation: OperationTemplate,
) -> Literal["ESTIMATED_HUMAN", "ESTIMATED_PREHEAT", "FIXED_PROCESS"]:
    if operation.duration.fixed_process_time or operation.action == "HEAT":
        return "FIXED_PROCESS"
    if operation.action == "PREHEAT":
        return "ESTIMATED_PREHEAT"
    if operation.action in ESTIMATED_ACTIONS and any(
        use.resource_type == "HUMAN" for use in operation.resource_requirements
    ):
        return "ESTIMATED_HUMAN"
    return "FIXED_PROCESS"


def source_estimates(inputs: DurationProblemInputs) -> tuple[DurationEstimate, ...]:
    """只读取发布事实及代理审核；不接收轨迹种子、未来速度或实际结束时间。"""
    recipes = {recipe.recipe_id: recipe for recipe in inputs.knowledge.recipes}
    ids = {root.recipe_id for root in inputs.roots}
    estimates = []
    devices = {item.device_instance_id: item for item in inputs.knowledge.devices}
    for recipe_id in sorted(ids, key=lambda item: item.root):
        recipe = recipes[recipe_id]
        for operation in recipe.operations:
            seconds = operation.duration.execution_sec
            if seconds is None or seconds <= 0:
                continue
            profile_refs = tuple(
                dict.fromkeys(
                    profile
                    for use in operation.resource_requirements
                    if use.resource_id in devices
                    for profile in devices[use.resource_id].capability_refs
                )
            )
            approval = recipe.approval
            estimates.append(
                DurationEstimate(
                    operation_template_id=f"{recipe_id.root}:{operation.operation_id.root}",
                    recipe_id=recipe_id,
                    operation_id=operation.operation_id.root,
                    operation_hash=content_hash(operation),
                    phase_type=estimated_phase(operation),
                    quantity_range="当前发布的原配方整份；不外推份量",
                    device_profile=profile_refs,
                    nominal_sec=seconds,
                    allowed_min_sec=operation.duration.lower_sec,
                    allowed_max_sec=operation.duration.upper_sec,
                    estimate_source="DELEGATED_REVIEWED_ESTIMATE"
                    if approval
                    else "SYNTHETIC_EXPERIMENT",
                    applicable_conditions=(
                        "固定原配方",
                        "单人人工",
                        inputs.knowledge.release.knowledge_version,
                    ),
                    data_version=inputs.policy.duration_data_version,
                    reviewer=approval.reviewer if approval else "synthetic:test-or-experiment",
                    review_evidence=approval.evidence_refs
                    if approval
                    else ("synthetic:explicit-duration-experiment",),
                )
            )
    return tuple(estimates)


class DurationPolicy:
    def apply(
        self, problem_inputs: DurationProblemInputs, estimates: tuple[DurationEstimate, ...]
    ) -> DurationAdjustedInputs:
        if len({estimate.operation_template_id for estimate in estimates}) != len(estimates):
            raise ValueError("时长估计身份重复")
        expected = {
            estimate.operation_template_id: estimate
            for estimate in source_estimates(problem_inputs)
        }
        for estimate in estimates:
            source = expected.get(estimate.operation_template_id)
            if source is None or any(
                getattr(estimate, name) != getattr(source, name)
                for name in (
                    "recipe_id",
                    "operation_id",
                    "operation_hash",
                    "phase_type",
                    "nominal_sec",
                    "allowed_min_sec",
                    "allowed_max_sec",
                    "data_version",
                )
            ):
                raise ValueError("时长估计偏离固定发布、版本或工艺事实")
            if (
                estimate.estimate_source == "SYNTHETIC_EXPERIMENT"
                and problem_inputs.knowledge.release.release_kind != "development"
            ):
                raise ValueError("合成时长估计不能用于正式发布策略")
        if problem_inputs.policy.duration_policy_id == "NOMINAL":
            return DurationAdjustedInputs(inputs=problem_inputs, estimates=estimates, buffers=())
        if set(expected) != {estimate.operation_template_id for estimate in estimates}:
            raise ValueError("BUFFERED 缺少对应来源估计")
        roots = defaultdict(list)
        for root in problem_inputs.roots:
            roots[root.recipe_instance_id].append(root)
        buffers = []
        for instance, members in roots.items():
            if any(member.instance_already_started for member in members):
                continue
            relevant = tuple(
                estimate
                for estimate in estimates
                if estimate.recipe_id == members[0].recipe_id
                and estimate.phase_type != "FIXED_PROCESS"
            )
            seconds = sum(estimate.nominal_sec for estimate in relevant)
            padding = (seconds * problem_inputs.policy.duration_buffer_basis_points + 9999) // 10000
            padding = align_up(padding, problem_inputs.policy.time_grid_sec)
            if padding == 0:
                continue
            base = max(
                problem_inputs.now_offset_sec, *(member.earliest_start_sec for member in members)
            )
            buffers.append(
                DurationBuffer(
                    recipe_instance_id=instance,
                    recipe_id=members[0].recipe_id,
                    root_task_ids=tuple(member.task_id for member in members),
                    buffer_sec=padding,
                    base_start_sec=base,
                    not_before_sec=base + padding,
                    estimate_refs=tuple(estimate.operation_template_id for estimate in relevant),
                    data_version=problem_inputs.policy.duration_data_version,
                )
            )
        return DurationAdjustedInputs(
            inputs=problem_inputs, estimates=estimates, buffers=tuple(buffers)
        )
