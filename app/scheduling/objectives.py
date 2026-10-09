"""分阶段目标和整数界；不将可行值宣称为已证明最优值。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.domain.base import MAX_INT
from app.domain.ids import TaskId
from app.domain.objectives import ObjectiveStage
from app.domain.policy import SchedulingPolicy
from app.domain.reports import SolverIndexMapping
from app.scheduling.human_chain_bounds import HumanChainBounds

if TYPE_CHECKING:
    from ortools.sat.python import cp_model

    from app.domain.scheduling_problem import CandidateCarrier
    from app.scheduling.model_builder import ModelBuilder


def makespan_cap(
    reference_sec: int, policy: SchedulingPolicy, serial_sec: int | None = None
) -> int:
    grid = policy.time_grid_sec
    ratio = policy.objective.makespan_extra_basis_points
    extra = ((reference_sec * ratio + 10_000 * grid - 1) // (10_000 * grid)) * grid
    cap = reference_sec + min(policy.objective.makespan_extra_cap_sec, extra)
    return min(cap, serial_sec) if serial_sec is not None else cap


def apply_stage(builder: ModelBuilder, stage: ObjectiveStage) -> None:
    model, problem = builder.model, builder.problem
    builder.objective_stage = stage.name
    builder.stage_parameters = stage
    before = len(model.proto.constraints)
    cooking_tasks = {b.recipe_instance_id: b.task_ids for b in problem.cooking_completions}
    completions = []
    for instance in problem.recipe_instances:
        builder.check_budget()
        end = model.new_int_var(
            0, problem.horizon_sec, "completion:" + instance.recipe_instance_id.root
        )
        model.add_max_equality(
            end,
            [builder.ends[task] for task in cooking_tasks[instance.recipe_instance_id]]
            if problem.policy.objective.spread_basis == "COOKING_FINISH"
            else [
                builder.ends[t.task_id]
                for t in problem.logical_tasks
                if t.recipe_instance_id == instance.recipe_instance_id
            ],
        )
        completions.append(end)
    earliest_completion = model.new_int_var(0, problem.horizon_sec, "earliest-completion")
    model.add_min_equality(earliest_completion, completions)
    latest_completion = model.new_int_var(0, problem.horizon_sec, "latest-completion")
    model.add_max_equality(latest_completion, completions)
    excess = model.new_int_var(0, problem.horizon_sec, "spread-excess")
    model.add_max_equality(
        excess,
        [0, latest_completion - earliest_completion - problem.policy.objective.spread_target_sec],
    )
    if stage.makespan_cap_sec is not None:
        model.add(builder.makespan <= stage.makespan_cap_sec)
    if stage.spread_excess_cap_sec is not None:
        model.add(excess <= stage.spread_excess_cap_sec)
    total_human = None
    continuous_quality = (
        stage.name == "E_QUALITY" and "HUMAN_BUSY" in problem.policy.objective.stages
    )
    if (
        stage.name == "D_TOTAL_HUMAN"
        or (stage.name == "E_QUALITY" and not continuous_quality)
        or stage.total_human_cap_sec is not None
    ):
        total_human = _total_human_work(builder)
        if stage.total_human_cap_sec is not None:
            model.add(total_human <= stage.total_human_cap_sec)
    human = None
    if stage.name == "D_HUMAN" or continuous_quality or stage.human_busy_cap_sec is not None:
        human = _human_busy(builder)
        if stage.human_busy_cap_sec is not None:
            model.add(human <= stage.human_busy_cap_sec)
    objective = (
        excess
        if stage.name == "B_SPREAD"
        else total_human
        if stage.name == "D_TOTAL_HUMAN"
        else human
        if stage.name == "D_HUMAN"
        else builder.makespan
    )
    if stage.name == "D_STABILITY":
        objective = _stability(builder, stage)
    if continuous_quality:
        assert human is not None
        horizon = problem.horizon_sec
        human_weight = horizon + 1
        spread_weight = human_weight * human_weight
        # 达标界固定后不编码常量零，避免长时间前处理将整数权重放大。
        spread_upper = 0 if stage.spread_excess_cap_sec == 0 else horizon
        upper = spread_upper * spread_weight + horizon * human_weight + horizon
        if upper > MAX_INT:
            raise ValueError("联合出锅/连续人工目标超出安全整数范围")
        objective = model.new_int_var(0, upper, "cooking-continuous-quality")
        model.add(objective == excess * spread_weight + human * human_weight + builder.makespan)
    elif stage.name == "E_QUALITY":
        # 精确整数词典序：超出目标秒数 > 总人工 > 总流程。
        # 权重覆盖后续目标的整个取值范围，不用任意经验权重交换优先级。
        assert total_human is not None
        constant = _constant_human_work(builder) if stage.spread_excess_cap_sec == 0 else None
        if constant is not None:
            # 完整覆盖与所有候选的人工等价已经证明总人工固定；集中出菜界
            # 也已固定为零。省去常量权重，保持同一词典序，只搜索真实总流程。
            model.add(total_human == constant)
            objective = builder.makespan
        else:
            horizon = problem.horizon_sec
            human_upper = total_human.proto.domain[len(total_human.proto.domain) - 1]
            human_weight = horizon + 1
            spread_weight = (human_upper + 1) * human_weight
            upper = horizon * spread_weight + human_upper * human_weight + horizon
            if upper > MAX_INT:
                raise ValueError("联合质量目标超出安全整数范围")
            objective = model.new_int_var(0, upper, "synchronized-quality")
            model.add(
                objective == excess * spread_weight + total_human * human_weight + builder.makespan
            )
    if objective is None:
        raise ValueError("优化目标尚未构造")
    builder.objective_variable = objective
    model.minimize(objective)
    builder.additional_mappings.append(
        SolverIndexMapping(
            constraint_id="objective-stage:" + stage.name,
            variable_ids=(objective.name,),
            proto_constraint_indices=tuple(range(before, len(model.proto.constraints))),
        )
    )


def _constant_human_work(builder: ModelBuilder) -> int | None:
    """保守检查每个合法载体的人工作量是否等于所覆盖原子需求之和。"""
    if builder.fixed or builder.problem.runtime.executions:
        return None

    def work(carrier: CandidateCarrier) -> int:
        return carrier.duration_sec * sum(
            use.resource_type == "HUMAN" for use in carrier.resource_uses
        ) + sum(
            phase.end_offset_sec - phase.start_offset_sec
            for phase in carrier.resource_phases
            if phase.resource_use.resource_type == "HUMAN"
        )

    costs: dict[TaskId, int] = {}
    for carrier in builder.problem.standalone_candidates:
        builder.check_budget()
        if len(carrier.covers) != 1:
            return None
        task = carrier.covers[0]
        value = work(carrier)
        if task in costs and costs[task] != value:
            return None
        costs[task] = value
    if set(costs) != {task.task_id for task in builder.problem.logical_tasks}:
        return None
    for carrier in builder.candidates:
        builder.check_budget()
        if any(task not in costs for task in carrier.covers) or work(carrier) != sum(
            costs[task] for task in carrier.covers
        ):
            return None
    return sum(costs.values())


def _total_human_work(builder: ModelBuilder) -> cp_model.IntVar:
    """共享载体的主动段计一次；真实历史由固定资源段计入。"""
    model, problem = builder.model, builder.problem
    contributions = []
    for index, phase in enumerate(builder.human_intervals):
        builder.check_budget()
        size = phase.interval.proto.interval.size
        phase_upper = size.offset if not size.vars else problem.horizon_sec
        value = model.new_int_var(0, phase_upper, f"human-work:{index}")
        model.add(value == phase.end - phase.start).only_enforce_if(phase.presence)
        model.add(value == 0).only_enforce_if(phase.presence.negated())
        contributions.append(value)
    upper = sum(value.proto.domain[len(value.proto.domain) - 1] for value in contributions)
    if upper > MAX_INT:
        raise ValueError("累计人工目标超出安全整数范围")
    total = model.new_int_var(0, upper, "total-human-work")
    model.add(total == sum(contributions))
    return total


def _human_busy(builder: ModelBuilder) -> cp_model.IntVar:
    model, problem = builder.model, builder.problem
    phases = builder.human_intervals
    if len(phases) > problem.policy.max_exact_human_phases:
        raise ValueError("人工精确阶段超过策略阈值")
    bounds = HumanChainBounds(builder)
    maximum = model.new_int_var(bounds.minimum_busy_sec, problem.horizon_sec, "max-human-busy")
    if not phases:
        model.add(maximum == 0)
        return maximum
    builder.human_chain_enabled = True
    beginnings = [
        model.new_int_var(0, problem.horizon_sec, f"busy-start:{i}") for i in range(len(phases))
    ]
    spans = []
    # 单人人工已由基础 NoOverlap 保护。任意两段之间不足休息阈值时，
    # 后段的块起点不晚于前段的起点；连续相邻段的传递闭包给出真实块。
    # 对每份合法排程，把起点设为真实块起点即可精确取值，因此不删任何
    # 未来顺序。目标最优值/可行上限与原环路模型等价，无需搜索整条环路。
    for i, phase in enumerate(phases):
        builder.check_budget()
        model.add(beginnings[i] <= phase.start).only_enforce_if(phase.presence)
        span = model.new_int_var(0, problem.horizon_sec, f"human-span:{i}")
        model.add(span == phase.end - beginnings[i]).only_enforce_if(phase.presence)
        model.add(span == 0).only_enforce_if(phase.presence.negated())
        spans.append(span)
    for i, phase in enumerate(phases):
        for j in range(i + 1, len(phases)):
            builder.check_budget()
            forward, backward = bounds.can_follow(i, j), bounds.can_follow(j, i)
            if not forward and not backward:
                continue
            active = [phase.presence, phases[j].presence]
            orders: tuple[tuple[int, int, cp_model.LiteralT], ...]
            if forward and backward:
                before = model.new_bool_var(f"human-before:{i}:{j}")
                orders = ((i, j, before), (j, i, before.negated()))
            else:
                orders = (
                    ((i, j, model.new_constant(1)),)
                    if forward
                    else ((j, i, model.new_constant(1)),)
                )
            for first, second, ordered in orders:
                _link_human_blocks(builder, beginnings, first, second, [*active, ordered])
    model.add_max_equality(maximum, spans)
    return maximum


def _link_human_blocks(
    builder: ModelBuilder,
    beginnings: list[cp_model.IntVar],
    first: int,
    second: int,
    enforcement: list[cp_model.LiteralT],
) -> None:
    model, problem = builder.model, builder.problem
    phases = builder.human_intervals
    builder.check_budget()
    builder.sequence_arcs += 1
    rested = model.new_bool_var(f"human-gap:{first}:{second}")
    gap = phases[second].start - phases[first].end
    model.add(gap >= 0).only_enforce_if(enforcement)
    model.add(gap >= problem.policy.objective.rest_gap_sec).only_enforce_if([*enforcement, rested])
    model.add(gap < problem.policy.objective.rest_gap_sec).only_enforce_if(
        [*enforcement, rested.negated()]
    )
    model.add(beginnings[second] <= beginnings[first]).only_enforce_if(
        [*enforcement, rested.negated()]
    )


def _stability(builder: ModelBuilder, stage: ObjectiveStage) -> cp_model.IntVar:
    previous = stage.previous_plan
    if previous is None:
        raise ValueError("稳定性目标需要明确的旧计划")
    model, problem = builder.model, builder.problem
    old = {t: a for a in previous.assignments for t in a.task_ids}
    common = sorted(
        (old.keys() & builder.starts.keys()) - builder.fixed.keys(), key=lambda t: t.root
    )
    shifts = []
    changes = []
    group_changes = []
    for task in common:
        builder.check_budget()
        shift = model.new_int_var(
            0, max(problem.horizon_sec, old[task].interval.start_sec), "shift:" + task.root
        )
        model.add_abs_equality(shift, builder.starts[task] - old[task].interval.start_sec)
        shifts.append(shift)
        prior_keys = {
            (u.resource_type, u.physical_resource_id, u.component_id, u.resource_id)
            for u in old[task].resource_uses
        }
        for candidate in builder.candidates:
            if task in candidate.covers:
                keys = {
                    (u.resource_type, u.physical_resource_id, u.component_id, u.resource_id)
                    for u in candidate.resource_uses
                }
                if keys != prior_keys:
                    changes.append(builder.selected[candidate.carrier_id])
                if set(candidate.covers) != set(old[task].task_ids):
                    group_changes.append(builder.selected[candidate.carrier_id])
    weight = len(common) + 1
    bound = (
        sum(max(problem.horizon_sec, old[t].interval.start_sec) for t in common) * weight * weight
        + len(common) * weight
        + len(common)
    )
    if bound > MAX_INT:
        raise ValueError("稳定性词典序编码超出安全整数范围")
    objective = model.new_int_var(0, bound, "stability")
    model.add(
        objective == sum(shifts) * weight * weight + sum(changes) * weight + sum(group_changes)
    )
    return objective
