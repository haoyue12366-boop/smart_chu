"""连续末段预约的容量下界；不固定菜序，也不删除加工或设备备选。"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import TYPE_CHECKING

from app.domain.carrier_timing import member_offsets
from app.domain.ids import TaskId

if TYPE_CHECKING:
    from app.domain.scheduling_problem import CandidateCarrier, TaskDependency
    from app.scheduling.model_builder import ModelBuilder

ResourceKey = tuple[str, str]


def minimum_finish_spread(builder: ModelBuilder) -> int:
    problem = builder.problem
    if problem.policy.objective.spread_basis != "COOKING_FINISH":
        return 0
    options: dict[TaskId, list[CandidateCarrier]] = defaultdict(list)
    for carrier in builder.candidates:
        builder.check_budget()
        for task in carrier.covers:
            options[task].append(carrier)
    duration, units = _task_minima(builder, options)
    conditional = [set(carrier.covers) for carrier in problem.inventory_supply_candidates]
    conditional.extend(set(item.task_ids) for item in problem.fixed_task_fulfillments)
    dependencies = [
        dep
        for dep in problem.dependencies
        if dep.min_lag_sec >= 0
        and not any({dep.predecessor_id, dep.successor_id} <= group for group in conditional)
    ]
    parents: dict[TaskId, set[TaskId]] = defaultdict(set)
    for dep in dependencies:
        parents[dep.successor_id].add(dep.predecessor_id)
    devices = {device.device_instance_id: device for device in problem.resources}
    capacities: dict[ResourceKey, int] = {}
    for device in problem.resources:
        capacities[device.competition_key] = max(
            capacities.get(device.competition_key, 0), device.capacity
        )
    replaced = {rid for c in problem.thermal_batch_candidates for rid in c.replaced_reservation_ids}
    finishes = {
        boundary.recipe_instance_id: boundary.task_ids[0]
        for boundary in problem.cooking_completions
        if len(boundary.task_ids) == 1
    }
    # 同一道菜、同一物理资源只取一份已证明的末段预约，避免重复计数。
    suffixes: dict[ResourceKey, dict[str, tuple[int, int]]] = defaultdict(dict)
    for reservation in problem.mandatory_programs.reservations:
        builder.check_budget()
        finish = finishes.get(reservation.recipe_instance_id)
        members = set(reservation.members)
        if (
            finish is None
            or finish not in members
            or members & builder.fixed.keys()
            or reservation.reservation_id in replaced
            or not members <= _ancestors(finish, parents, builder)
        ):
            continue
        keys = {
            devices[rid].competition_key for rid in reservation.resource_options if rid in devices
        }
        if len(keys) != 1 or any(rid not in devices for rid in reservation.resource_options):
            continue  # 有多个物理设备备选时，不能假定它必占某一台。
        key = next(iter(keys))
        if capacities[key] == 1 and reservation.conflict_policy not in {"UNARY", "BATCH_EXCLUSIVE"}:
            # 冰箱/辅助设备的兼容共享不由默认 capacity=1 限制数量。
            continue
        amount = min((units.get(task, {}).get(key, 0) for task in members), default=0)
        if amount == 0 or amount > capacities[key]:
            continue
        length = _path_length(members, finish, dependencies, duration, builder)
        root = reservation.recipe_instance_id.root
        suffixes[key][root] = max(suffixes[key].get(root, (0, 0)), (amount, length))
    return max(
        (
            _capacity_width(tuple(values.values()), capacities[key])
            for key, values in suffixes.items()
        ),
        default=0,
    )


def _task_minima(
    builder: ModelBuilder, options: dict[TaskId, list[CandidateCarrier]]
) -> tuple[dict[TaskId, int], dict[TaskId, dict[ResourceKey, int]]]:
    duration: dict[TaskId, int] = {}
    units: dict[TaskId, dict[ResourceKey, int]] = {}
    for task, carriers in options.items():
        builder.check_budget()
        if task in builder.fixed or any(len(carrier.covers) != 1 for carrier in carriers):
            continue  # 共批或共享载体不能按多份独立预约重复计算。
        duration[task] = min(
            end - start for carrier in carriers for start, end in (member_offsets(carrier)[task],)
        )
        requirements = []
        for carrier in carriers:
            resources: dict[ResourceKey, int] = {}
            for use in carrier.resource_uses:
                if (
                    use.resource_type == "DEVICE"
                    and use.occupation_policy == "WHOLE_INTERVAL"
                    and use.physical_resource_id is not None
                    and use.component_id is not None
                ):
                    key = use.physical_resource_id, use.component_id
                    resources[key] = max(resources.get(key, 0), use.units)
            requirements.append(resources)
        common = set(requirements[0]).intersection(*requirements[1:])
        units[task] = {key: min(item[key] for item in requirements) for key in common}
    return duration, units


def _ancestors(
    finish: TaskId, parents: dict[TaskId, set[TaskId]], builder: ModelBuilder
) -> set[TaskId]:
    reached = {finish}
    pending = [finish]
    while pending:
        builder.check_budget()
        for task in parents[pending.pop()]:
            if task not in reached:
                reached.add(task)
                pending.append(task)
    return reached


def _path_length(
    members: set[TaskId],
    finish: TaskId,
    dependencies: list[TaskDependency],
    duration: dict[TaskId, int],
    builder: ModelBuilder,
) -> int:
    following: dict[TaskId, list[tuple[TaskId, int]]] = defaultdict(list)
    indegree = dict.fromkeys(members, 0)
    longest = {task: duration.get(task, 0) for task in members}
    for dep in dependencies:
        if dep.predecessor_id in members and dep.successor_id in members:
            following[dep.predecessor_id].append((dep.successor_id, dep.min_lag_sec))
            indegree[dep.successor_id] += 1
    pending = deque(task for task in members if indegree[task] == 0)
    visited = 0
    while pending:
        builder.check_budget()
        task = pending.popleft()
        visited += 1
        for successor, lag in following[task]:
            longest[successor] = max(
                longest[successor], longest[task] + lag + duration.get(successor, 0)
            )
            indegree[successor] -= 1
            if indegree[successor] == 0:
                pending.append(successor)
    return longest[finish] if visited == len(members) else 0


def _capacity_width(suffixes: tuple[tuple[int, int], ...], capacity: int) -> int:
    # 每份预约连续占用最后至少 L 秒。若所有出锅时刻极差小于 L，
    # 这些预约必在最早出锅前共同占用；用量超容量即矛盾。
    # 左闭右开允许极差恰好 L，故这里取安全的 >= L 下界。
    amount = 0
    for units, length in sorted(suffixes, key=lambda value: value[1], reverse=True):
        amount += units
        if amount > capacity:
            return length
    return 0
