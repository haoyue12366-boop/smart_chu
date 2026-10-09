"""有限确定性菜内布局；布局限制不改变 CP-SAT 的候选或时间域。"""

import time

from app.domain.carrier_timing import member_offsets
from app.domain.ids import RecipeInstanceId, TaskId
from app.domain.ports import Deadline
from app.domain.schedule import ScheduledAssignment
from app.domain.scheduling_problem import CandidateCarrier, SchedulingProblem
from app.domain.time import Interval, align_up
from app.scheduling.calendar_projection import (
    capacity_collision,
    conflicts,
    entries_for,
    fixed_ports,
)


def recipe_layout(
    problem: SchedulingProblem,
    instance: RecipeInstanceId | None,
    variant: int,
    deadline: Deadline,
    shared_candidates: tuple[CandidateCarrier, ...] = (),
) -> tuple[ScheduledAssignment, ...]:
    tasks = [
        t for t in problem.logical_tasks if instance is None or t.recipe_instance_id == instance
    ]
    ids = {t.task_id for t in tasks}
    frozen = {p.task_id: p.interval for p in fixed_ports(problem) if p.task_id in ids}
    devices = {d.device_instance_id: d for d in problem.resources}
    required_keys: dict[TaskId, set[tuple[str, str]]] = {t.task_id: set() for t in tasks}
    for i, reservation in enumerate(problem.mandatory_programs.reservations):
        if instance is not None and reservation.recipe_instance_id != instance:
            continue
        device_options = sorted({devices[r].competition_key for r in reservation.resource_options})
        actual = {
            devices[r].competition_key
            for e in problem.fixed_executions
            if set(e.task_ids) & set(reservation.members)
            for r in e.resource_ids
            if r in devices and devices[r].competition_key in device_options
        }
        if len(actual) > 1 or not device_options:
            raise ValueError("固定批次资源冲突")
        chosen = (
            next(iter(actual)) if actual else device_options[(variant + i) % len(device_options)]
        )
        for member_id in reservation.members:
            required_keys[member_id].add(chosen)
    selected: dict[TaskId, CandidateCarrier] = {}
    proposed_shared = {task: carrier for carrier in shared_candidates for task in carrier.covers}
    standalone_by_task: dict[str, list[CandidateCarrier]] = {}
    for carrier in problem.standalone_candidates:
        if len(carrier.covers) == 1:
            standalone_by_task.setdefault(carrier.covers[0].root, []).append(carrier)
    for task in tasks:
        if task.task_id in frozen:
            continue
        if task.task_id in proposed_shared:
            selected[task.task_id] = proposed_shared[task.task_id]
            continue
        options = [
            c
            for c in standalone_by_task.get(task.task_id.root, ())
            if required_keys[task.task_id]
            <= {
                (u.physical_resource_id, u.component_id)
                for u in c.resource_uses
                if u.resource_type == "DEVICE"
            }
        ]
        if not options:
            raise ValueError("当前菜内布局没有一致的设备选择")
        selected[task.task_id] = options[variant % len(options)]
    shared_tasks: set[TaskId] = set()
    for carrier in shared_candidates:
        if shared_tasks.intersection(carrier.covers) or any(t in frozen for t in carrier.covers):
            raise ValueError("共享布局覆盖重叠或已发生工序")
        if not set(carrier.covers) <= ids:
            raise ValueError("共享布局缺少完整成员")
        shared_tasks.update(carrier.covers)
        selected.update((t, carrier) for t in carrier.covers)
    relative = {t: member_offsets(c)[t] for t, c in selected.items()}
    durations = {
        t.task_id: (
            frozen[t.task_id].end_sec - frozen[t.task_id].start_sec
            if t.task_id in frozen
            else relative[t.task_id][1] - relative[t.task_id][0]
        )
        for t in tasks
    }
    edges: list[tuple[TaskId, TaskId, int]] = []
    for carrier in shared_candidates:
        first = carrier.covers[0]
        for other in carrier.covers[1:]:
            delta = relative[other][0] - relative[first][0]
            edges.extend(((first, other, delta), (other, first, -delta)))
    for dep in problem.dependencies:
        if any(
            carrier.kind == "INVENTORY_SUPPLY"
            and {dep.predecessor_id, dep.successor_id} <= set(carrier.covers)
            for carrier in shared_candidates
        ) or any(
            {dep.predecessor_id, dep.successor_id} <= set(item.task_ids)
            for item in problem.fixed_task_fulfillments
        ):
            continue
        if dep.predecessor_id in ids and dep.successor_id in ids:
            edges.append(
                (
                    dep.predecessor_id,
                    dep.successor_id,
                    durations[dep.predecessor_id] + dep.min_lag_sec,
                )
            )
            if dep.max_lag_sec is not None:
                edges.append(
                    (
                        dep.successor_id,
                        dep.predecessor_id,
                        -durations[dep.predecessor_id] - dep.max_lag_sec,
                    )
                )
    for relation in problem.mandatory_programs.time_relations:
        if relation.left_task not in ids or relation.right_task not in ids:
            continue
        delta = (durations[relation.left_task] if relation.left_anchor == "END" else 0) - (
            durations[relation.right_task] if relation.right_anchor == "END" else 0
        )
        edges.append((relation.left_task, relation.right_task, delta + relation.min_offset_sec))
        if relation.max_offset_sec is not None:
            edges.append(
                (relation.right_task, relation.left_task, -delta - relation.max_offset_sec)
            )
    grid = problem.policy.time_grid_sec
    frozen_roots = frozenset(task.root for task in frozen)
    initial_starts = {
        t.task_id.root: (
            frozen[t.task_id].start_sec
            if t.task_id in frozen
            else (
                max(problem.runtime.now_offset_sec, t.earliest_start_sec)
                if instance is None
                else (problem.runtime.now_offset_sec if frozen else 0)
            )
        )
        for t in tasks
    }

    def relax(constraints: list[tuple[TaskId, TaskId, int]]) -> dict[TaskId, int] | None:
        starts = dict(initial_starts)
        root_constraints = [(left.root, right.root, weight) for left, right, weight in constraints]
        for _ in range(len(tasks) + 1):
            if time.monotonic_ns() >= deadline.expires_at_ns:
                raise TimeoutError("菜内布局截止时间已到")
            changed = False
            for left, right, weight in root_constraints:
                lower = starts[left] + weight
                if right not in frozen_roots:
                    lower = align_up(lower, grid)
                if lower > starts[right]:
                    if right in frozen_roots:
                        return None
                    starts[right] = lower
                    changed = True
            if not changed:
                return {t.task_id: starts[t.task_id.root] for t in tasks}
        return None

    def assignments(starts: dict[TaskId, int]) -> tuple[ScheduledAssignment, ...]:
        return tuple(
            ScheduledAssignment(
                carrier_id=c.carrier_id,
                task_ids=c.covers,
                interval=Interval(
                    start_sec=starts[tid] - relative[tid][0],
                    end_sec=starts[tid] - relative[tid][0] + c.duration_sec,
                ),
                resource_uses=c.resource_uses,
            )
            for tid, c in selected.items()
            if tid == c.covers[0]
        )

    for _ in range(max(1, len(tasks) ** 2)):
        starts = relax(edges)
        if starts is None:
            raise ValueError("当前菜内布局的窗口冲突")
        proposal = assignments(starts)
        entries = [
            e for e in entries_for(problem, proposal, include_fixed=True) if set(e.task_ids) <= ids
        ]
        entries.sort(key=lambda e: (e.interval.start_sec, min(t.root for t in e.task_ids)))
        collision = next(
            (
                (left, right)
                for i, left in enumerate(entries)
                for right in entries[i + 1 :]
                if conflicts(left, right)
            ),
            None,
        )
        if collision is None:
            collision = capacity_collision(problem, entries)
        if collision is None:
            if instance is None and any(
                starts[t.task_id] + durations[t.task_id]
                > (t.latest_end_sec if t.latest_end_sec is not None else problem.horizon_sec)
                for t in tasks
            ):
                raise ValueError("全菜单共享布局违反硬时间窗口")
            return proposal
        left, right = collision
        if variant == 1:
            left, right = right, left
        forward = [
            (a, b, left.interval.end_sec - starts[a] - (right.interval.start_sec - starts[b]))
            for a in left.task_ids
            for b in right.task_ids
        ]
        backward = [
            (b, a, right.interval.end_sec - starts[b] - (left.interval.start_sec - starts[a]))
            for a in left.task_ids
            for b in right.task_ids
        ]
        if relax(edges + forward) is not None:
            edges += forward
        elif relax(edges + backward) is not None:
            edges += backward
        else:
            raise ValueError("当前内部资源排列与固定工艺冲突")
    raise ValueError("有限菜内布局搜索耗尽")
