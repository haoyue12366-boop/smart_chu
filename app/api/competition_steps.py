"""以选中载体及实际历史投影工序，显式保留批次内部阶段。"""

from dataclasses import dataclass

from app.api.minute_projection import minute_text
from app.domain.carrier_timing import task_intervals
from app.domain.execution_timing import execution_intervals, execution_resource_uses
from app.domain.ids import TaskId
from app.domain.resources import ResourceUse
from app.domain.schedule import PublishedPlan
from app.domain.scheduling_problem import LogicalTask, SchedulingProblem
from app.domain.time import Interval


@dataclass(frozen=True)
class ProjectedTask:
    task: LogicalTask
    interval: Interval
    owner: str
    resources: tuple[ResourceUse, ...]
    inventory: bool = False


def projected_tasks(plan: PublishedPlan, problem: SchedulingProblem) -> tuple[ProjectedTask, ...]:
    spans = task_intervals(problem, plan.validated.candidate.assignments)
    carriers = {
        c.carrier_id: c
        for c in (
            *problem.standalone_candidates,
            *problem.shared_prep_candidates,
            *problem.thermal_batch_candidates,
            *problem.inventory_supply_candidates,
        )
    }
    owners: dict[TaskId, str] = {}
    resources: dict[TaskId, tuple[ResourceUse, ...]] = {}
    supplied: set[TaskId] = set()
    for assignment in plan.validated.candidate.assignments:
        carrier = carriers[assignment.carrier_id]
        for tid in assignment.task_ids:
            owners[tid] = assignment.carrier_id.root
            phases = tuple(p.resource_use for p in carrier.resource_phases if p.task_id == tid)
            resources[tid] = phases or carrier.resource_uses
            if carrier.kind == "INVENTORY_SUPPLY":
                supplied.add(tid)
                resources[tid] = ()
    for fulfilment in problem.fixed_supply_fulfillments:
        for tid in fulfilment.task_ids:
            spans[tid] = Interval(
                start_sec=fulfilment.satisfied_at_sec, end_sec=fulfilment.satisfied_at_sec
            )
            owners[tid] = "inventory:" + fulfilment.fulfillment_id
            resources[tid] = ()
            supplied.add(tid)
    for record in problem.fixed_executions:
        for tid, interval in execution_intervals(record, problem.runtime).items():
            spans[tid] = interval
            owners[tid] = record.carrier_id or record.execution_id.root
            task = next((t for t in problem.logical_tasks if t.task_id == tid), None)
            if task is not None:
                resources[tid] = execution_resource_uses(
                    record, interval, task.operation.resource_requirements, problem.resources
                )
    tasks = []
    prepared = {task for item in problem.advance_preparations for task in item.task_ids}
    for task in problem.logical_tasks:
        if task.task_id in prepared:
            if task.task_id in spans:
                raise ValueError("开工前准备不能重复安排加工")
            continue
        if task.task_id not in spans:
            raise ValueError("已发布计划缺少必需工序或实际历史的时间映射")
        uses = resources.get(task.task_id, task.operation.resource_requirements)
        tasks.append(
            ProjectedTask(
                task, spans[task.task_id], owners[task.task_id], uses, task.task_id in supplied
            )
        )
    return tuple(tasks)


def device_description(
    step: ProjectedTask, problem: SchedulingProblem
) -> tuple[str, str, int | str]:
    device = next((u for u in step.resources if u.resource_type == "DEVICE"), None)
    if device is None:
        return "人工", "手工操作", "不涉及温度"
    profiles = {p.profile_id: p for p in problem.device_profiles}
    profile = next((profiles[ref] for ref in device.profile_options if ref in profiles), None)
    configuration = {item.parameter: item.value for item in device.configuration}
    # 物理设备由已选载体提供，工序温度和模式保留各自审核参数。
    own = next(
        (u for u in step.task.operation.resource_requirements if u.resource_type == "DEVICE"), None
    )
    if own is not None:
        configuration.update((item.parameter, item.value) for item in own.configuration)
        profile = next((profiles[ref] for ref in own.profile_options if ref in profiles), profile)
    temperature = next(
        (
            configuration[key]
            for key in ("temperature_c", "temperature", "power_level", "fire_level")
            if key in configuration
        ),
        "按审核程序",
    )
    return (
        profile.device_type if profile else device.resource_id,
        profile.mode if profile else "按审核程序",
        temperature,
    )


def step_kind(step: ProjectedTask, problem: SchedulingProblem) -> tuple[int, str]:
    if step.inventory:
        return 1, "已有备料"
    action = step.task.operation.action
    device, _, _ = device_description(step, problem)
    if action == "PREHEAT":
        return 5, "预热"
    if action in {"MARINATE", "WAIT", "CHILL", "FREEZE"}:
        return 2, "腌制备料" if action == "MARINATE" else "等待备料"
    if action in {"FINISH", "UNLOAD", "MILESTONE"}:
        return 6, "出锅装盘" if action != "MILESTONE" else "制作完成"
    if action == "HEAT":
        if "蒸" in device or "烤" in device or any(x in device.lower() for x in ("steam", "oven")):
            return 5, "蒸烤"
        description = step.task.operation.description
        return (4, "炒制") if any(x in description for x in ("炒", "煎", "炸")) else (3, "炖煮")
    return 1, "食材处理"


def step_description(step: ProjectedTask, problem: SchedulingProblem) -> str:
    operation = step.task.operation
    text = operation.description or operation.action.value
    if step.inventory:
        return "已有合格备料满足：" + text
    device, mode, temperature = device_description(step, problem)
    if operation.action == "MARINATE":
        return "将食材腌制：" + text
    if operation.action in {"HEAT", "PREHEAT"}:
        duration = minute_text(step.interval.end_sec - step.interval.start_sec)
        return f"将食材放入{device}，设置{mode}，温度/火力{temperature}，时间{duration}分钟；{text}"
    if operation.action in {"FINISH", "UNLOAD"}:
        return "出锅/装盘：" + text
    return "准备食材备用：" + text
