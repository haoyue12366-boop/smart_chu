"""直接扫描不可变问题中的选中载体与实际历史，不调用 Adapter 或编译约束。"""

from dataclasses import dataclass

from app.domain.execution_timing import execution_intervals, execution_resource_uses
from app.domain.ids import TaskId
from app.domain.resources import ResourceUse
from app.domain.schedule import PublishedPlan
from app.domain.scheduling_problem import LogicalTask, SchedulingProblem


@dataclass(frozen=True)
class ProjectionFact:
    task: LogicalTask
    start: int
    end: int
    owner: str
    resources: tuple[ResourceUse, ...]
    inventory: bool = False


def projection_facts(plan: PublishedPlan, problem: SchedulingProblem) -> tuple[ProjectionFact, ...]:
    tasks = {task.task_id: task for task in problem.logical_tasks}
    carriers = {
        c.carrier_id: c
        for collection in (
            problem.standalone_candidates,
            problem.shared_prep_candidates,
            problem.thermal_batch_candidates,
            problem.inventory_supply_candidates,
        )
        for c in collection
    }
    found: dict[TaskId, ProjectionFact] = {}
    for assignment in plan.validated.candidate.assignments:
        carrier = carriers[assignment.carrier_id]
        offsets = {
            p.task_id: (p.start_offset_sec, p.end_offset_sec) for p in carrier.member_offsets
        }
        for tid in assignment.task_ids:
            if tid in found:
                raise ValueError("响应依据包含重复工序")
            low, high = offsets.get(tid, (0, carrier.duration_sec))
            resources = tuple(p.resource_use for p in carrier.resource_phases if p.task_id == tid)
            found[tid] = ProjectionFact(
                tasks[tid],
                assignment.interval.start_sec + low,
                assignment.interval.start_sec + high,
                carrier.carrier_id.root,
                resources or carrier.resource_uses,
                carrier.kind == "INVENTORY_SUPPLY",
            )
    for fulfilment in problem.fixed_supply_fulfillments:
        for tid in fulfilment.task_ids:
            if tid not in tasks:
                continue
            if tid in found:
                raise ValueError("已有供应被重复加工")
            found[tid] = ProjectionFact(
                tasks[tid],
                fulfilment.satisfied_at_sec,
                fulfilment.satisfied_at_sec,
                "inventory:" + fulfilment.fulfillment_id,
                (),
                True,
            )
    for record in problem.fixed_executions:
        for tid, span in execution_intervals(record, problem.runtime).items():
            if tid not in tasks:
                continue
            if tid in found:
                raise ValueError("冻结工序被再次排程")
            found[tid] = ProjectionFact(
                tasks[tid],
                span.start_sec,
                span.end_sec,
                record.carrier_id or record.execution_id.root,
                execution_resource_uses(
                    record, span, tasks[tid].operation.resource_requirements, problem.resources
                ),
            )
    prepared = {task for item in problem.advance_preparations for task in item.task_ids}
    if not prepared <= tasks.keys() or set(found) != set(tasks) - prepared:
        raise ValueError("投影依据未恰好覆盖全部必需工序")
    return tuple(found.values())


def parameters(fact: ProjectionFact, problem: SchedulingProblem) -> tuple[str, str, int | str]:
    chosen = next((use for use in fact.resources if use.resource_type == "DEVICE"), None)
    if chosen is None:
        return "人工", "手工操作", "不涉及温度"
    profile_ids = chosen.profile_options
    values = {v.parameter: v.value for v in chosen.configuration}
    original = next(
        (use for use in fact.task.operation.resource_requirements if use.resource_type == "DEVICE"),
        None,
    )
    if original is not None:
        profile_ids = original.profile_options or profile_ids
        values.update({v.parameter: v.value for v in original.configuration})
    profile = next(
        (p for ref in profile_ids for p in problem.device_profiles if p.profile_id == ref), None
    )
    value = next(
        (
            values[key]
            for key in ("temperature_c", "temperature", "power_level", "fire_level")
            if key in values
        ),
        "按审核程序",
    )
    return (
        profile.device_type if profile else chosen.resource_id,
        profile.mode if profile else "按审核程序",
        value,
    )


def kind(fact: ProjectionFact, problem: SchedulingProblem) -> int:
    if fact.inventory:
        return 1
    action = fact.task.operation.action
    if action == "PREHEAT":
        return 5
    if action in {"MARINATE", "WAIT", "CHILL", "FREEZE"}:
        return 2
    if action in {"FINISH", "UNLOAD", "MILESTONE"}:
        return 6
    if action != "HEAT":
        return 1
    device, _, _ = parameters(fact, problem)
    if any(token in device.lower() for token in ("oven", "steam", "蒸", "烤")):
        return 5
    return 4 if any(token in fact.task.operation.description for token in ("炒", "煎", "炸")) else 3
