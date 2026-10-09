"""建模前保守规模预估；软阈值只报告，不删除约束或候选。"""

from app.domain.base import FrozenModel, NonEmpty, NonNegativeInt
from app.domain.policy import ModelSize
from app.domain.scheduling_problem import SchedulingProblem


class ModelSizeEstimate(FrozenModel):
    size: ModelSize
    estimated_proto_bytes: NonNegativeInt
    exceeded_limits: tuple[NonEmpty, ...] = ()
    candidate_truncated: bool = False


def estimate_model_size(problem: SchedulingProblem) -> ModelSizeEstimate:
    candidates = (
        *problem.standalone_candidates,
        *problem.shared_prep_candidates,
        *problem.thermal_batch_candidates,
        *problem.inventory_supply_candidates,
    )
    count = len(candidates)
    tasks = len(problem.logical_tasks)
    phases = sum(len(c.resource_uses) + len(c.resource_phases) for c in candidates)
    reservations = len(problem.mandatory_programs.reservations)
    layer_slots = sum(
        device.capacity * (phases + reservations + len(problem.resource_blocks))
        for device in problem.resources
        if device.capacity > 1
    )
    # 包含候选选择、起止时刻、任务端口、外层预约和最坏的两两次序布尔量。
    arcs = (phases + reservations) * max(0, phases + reservations - 1) // 2
    booleans = count + arcs + layer_slots
    constraints = (
        tasks * 4
        + count * 4
        + 2 * sum(len(c.member_offsets) for c in candidates)
        + phases * 2
        + arcs * 2
        + len(problem.dependencies) * 2
        + reservations * 4
        + len(problem.material_flow.demands) * 2
        + len(problem.mandatory_programs.time_relations) * 2
        + len(problem.fixed_executions) * 4
        + len(problem.resource_blocks)
        + 4 * len(problem.material_allocations.allocations)
        + 2 * len(problem.material_allocations.remainders)
        + layer_slots * 3
    )
    size = ModelSize(
        total_variables=booleans
        + 2 * (tasks + count + reservations)
        + len(problem.material_allocations.allocations)
        + layer_slots * 2,
        boolean_variables=booleans,
        optional_intervals=phases + reservations + layer_slots,
        total_constraints=constraints,
        sequence_arcs=arcs,
    )
    limits = problem.policy.model_soft_limits
    exceeded = tuple(
        name for name in ModelSize.model_fields if getattr(size, name) > getattr(limits, name)
    )
    return ModelSizeEstimate(
        size=size,
        estimated_proto_bytes=size.total_variables * 96 + constraints * 160,
        exceeded_limits=exceeded,
        candidate_truncated=problem.candidate_generation_report.candidate_truncated,
    )
