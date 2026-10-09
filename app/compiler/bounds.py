"""以全部保留候选的最短/最长时长推导安全域，不添加官方截止时间。"""

from app.compiler.dependency_graph import DependencyGraph
from app.domain.advance_preparation import AdvancePreparation, active_preparations
from app.domain.base import Digest, FrozenModel, NonNegativeInt, PositiveInt, content_hash
from app.domain.carrier_timing import member_offsets
from app.domain.execution_timing import execution_intervals
from app.domain.ids import TaskId
from app.domain.inventory import InventoryFulfillment, committed_fulfillments
from app.domain.policy import SchedulingPolicy
from app.domain.runtime_snapshot import RuntimeSnapshot
from app.domain.scheduling_problem import CandidateCarrier
from app.domain.time import align_down, align_up, checked_seconds


class TaskTimeBound(FrozenModel):
    task_id: TaskId
    earliest_start_sec: NonNegativeInt
    latest_end_sec: NonNegativeInt
    minimum_duration_sec: NonNegativeInt
    maximum_duration_sec: NonNegativeInt


class TimeBounds(FrozenModel):
    tasks: tuple[TaskTimeBound, ...]
    horizon_sec: PositiveInt
    makespan_lower_bound_sec: NonNegativeInt
    input_hash: Digest


class _BoundInput(FrozenModel):
    graph: DependencyGraph
    candidates: tuple[CandidateCarrier, ...]
    runtime: RuntimeSnapshot
    policy: SchedulingPolicy


def derive_bounds(
    graph: DependencyGraph,
    candidates: tuple[CandidateCarrier, ...],
    runtime: RuntimeSnapshot,
    policy: SchedulingPolicy,
) -> TimeBounds:
    grid = policy.time_grid_sec
    tasks = {t.task_id: t for t in graph.tasks}
    if not tasks:
        if not (
            runtime.details
            and runtime.details.planning_kind == "REPLAN"
            and runtime.details.cancelled_instance_ids
        ):
            raise ValueError("空任务没有可构建时间域")
        return TimeBounds(
            tasks=(),
            horizon_sec=align_up(max(1, runtime.now_offset_sec + 1), grid),
            makespan_lower_bound_sec=0,
            input_hash=content_hash(
                _BoundInput(graph=graph, candidates=candidates, runtime=runtime, policy=policy)
            ),
        )
    durations: dict[TaskId, list[int]] = {key: [] for key in tasks}
    for candidate in candidates:
        for key, (start_offset, end_offset) in member_offsets(candidate).items():
            if key not in tasks:
                raise ValueError("候选覆盖未知任务")
            durations[key].append(end_offset - start_offset)
    lower = {
        key: align_up(max(t.earliest_start_sec, runtime.now_offset_sec), grid)
        for key, t in tasks.items()
    }
    fixed: dict[TaskId, tuple[int, int]] = {}
    fulfillments: tuple[InventoryFulfillment | AdvancePreparation, ...] = (
        *committed_fulfillments(runtime),
        *active_preparations(runtime),
    )
    for fulfillment in fulfillments:
        for key in fulfillment.task_ids:
            if key not in tasks or key in fixed:
                raise ValueError("库存满足记录引用冲突")
            fixed[key] = fulfillment.satisfied_at_sec, fulfillment.satisfied_at_sec
            lower[key] = fulfillment.satisfied_at_sec
            durations[key] = [0]
    for execution in runtime.executions:
        if execution.status not in {"COMPLETED", "RUNNING"}:
            continue
        if runtime.details and set(execution.task_ids) <= set(runtime.details.retired_task_ids):
            continue
        if execution.started_at is None:
            raise ValueError("冻结执行缺少开始事实")
        start = runtime.time_origin.offset(execution.started_at)
        spans = execution_intervals(execution, runtime)
        for key in execution.task_ids:
            if key not in tasks:
                if runtime.details and key in runtime.details.retired_task_ids:
                    continue
                raise ValueError("冻结执行引用冲突")
            if key in fixed:
                raise ValueError("冻结执行引用冲突")
            if execution.finished_at:
                end = runtime.time_origin.offset(execution.finished_at)
            else:
                if execution.remaining_sec is None or not execution.remaining_source_ref:
                    raise ValueError("运行中工序缺少有来源的明确剩余时长")
                end = runtime.now_offset_sec + execution.remaining_sec
            if start < 0 or end < start:
                raise ValueError("冻结执行时间域超出当前原点")
            start, end = spans[key].start_sec, spans[key].end_sec
            fixed[key] = start, end
            lower[key] = start
            durations[key] = [end - start]
    if any(not values for values in durations.values()):
        raise ValueError("有未满足需求缺少候选或冻结事实")
    minimum = {key: min(values) for key, values in durations.items()}
    maximum = {key: max(values) for key, values in durations.items()}
    if any(
        value % grid for key, values in durations.items() if key not in fixed for value in values
    ):
        raise ValueError("工序时长不能在所选整数网格表达；不能独立取整")
    edge_bounds = []
    for edge in graph.dependencies:
        inventory_internal = any(
            candidate.kind == "INVENTORY_SUPPLY"
            and {edge.predecessor_id, edge.successor_id} <= set(candidate.covers)
            for candidate in candidates
        ) or any(
            {edge.predecessor_id, edge.successor_id} <= set(item.task_ids) for item in fulfillments
        )
        low = 0 if inventory_internal else align_up(edge.min_lag_sec, grid)
        high = align_down(edge.max_lag_sec, grid) if edge.max_lag_sec is not None else None
        if high is not None and low > high:
            raise ValueError("工序时间域的最小/最大间隔矛盾")
        edge_bounds.append((edge.predecessor_id, edge.successor_id, low, high))
    recovery = max(
        (
            runtime.time_origin.offset(d.expected_recovery_at)
            for d in runtime.device_states
            if d.expected_recovery_at
        ),
        default=0,
    )
    horizon = align_up(
        checked_seconds(
            max(1, recovery, *lower.values())
            + sum(maximum.values())
            + sum(e[2] for e in edge_bounds)
            + policy.objective.makespan_extra_cap_sec
        ),
        grid,
    )
    upper = {
        key: min(horizon, task.latest_end_sec if task.latest_end_sec is not None else horizon)
        for key, task in tasks.items()
    }
    for key, (_start, end) in fixed.items():
        upper[key] = end
    # 关闭图域收紧仅保留原始域、运行冻结与同一安全 horizon；依赖/间隔
    # 仍完整进入 CP-SAT 和独立 Validator，不移除工艺硬约束。
    for _ in range(len(tasks) + 1 if policy.graph_bound_preprocessing else 0):
        changed = False
        for before, after, low, high in edge_bounds:
            proposed = lower[before] + minimum[before] + low
            if proposed > lower[after]:
                lower[after] = proposed
                changed = True
            if high is not None:
                proposed = lower[after] - maximum[before] - high
                if proposed > lower[before]:
                    lower[before] = proposed
                    changed = True
        if any(lower[key] + minimum[key] > upper[key] for key in tasks):
            raise ValueError("最早完成晚于时间域上界或冻结历史冲突")
        if not changed:
            break
    else:
        if policy.graph_bound_preprocessing:
            raise ValueError("时间域约束存在正环")
    if any(lower[key] + minimum[key] > upper[key] for key in tasks):
        raise ValueError("最早完成晚于时间域上界或冻结历史冲突")
    return TimeBounds(
        tasks=tuple(
            TaskTimeBound(
                task_id=key,
                earliest_start_sec=lower[key],
                latest_end_sec=upper[key],
                minimum_duration_sec=minimum[key],
                maximum_duration_sec=maximum[key],
            )
            for key in tasks
        ),
        horizon_sec=horizon,
        makespan_lower_bound_sec=max(lower[key] + minimum[key] for key in tasks),
        input_hash=content_hash(
            _BoundInput(graph=graph, candidates=candidates, runtime=runtime, policy=policy)
        ),
    )
