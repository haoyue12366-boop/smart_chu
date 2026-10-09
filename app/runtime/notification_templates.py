"""说明只填入固定知识与已发布工序参数，不调用模型。"""

from app.domain.execution_timing import execution_intervals, execution_resource_uses
from app.domain.ids import TaskId
from app.domain.resources import ResourceUse
from app.domain.schedule import PublishedPlan
from app.domain.scheduling_problem import SchedulingProblem


def plan_change_notice(plan: PublishedPlan) -> dict[str, object]:
    identity = "plan-changed:" + plan.publication_id
    return {
        "notification_id": identity,
        "event_id": identity,
        "plan_version": plan.plan_version,
        "kind": "PLAN_CHANGED",
        "text": f"计划已更新为第 {plan.plan_version} 版，请查看最新操作安排。",
        "data": {
            "publication_id": plan.publication_id,
            "state_revision": plan.state_revision,
        },
    }


def operation_notice(
    problem: SchedulingProblem, task_ids: tuple[TaskId, ...], kind: str, plan: PublishedPlan
) -> str:
    names = {r.recipe_instance_id: r.name for r in problem.recipe_instances}
    profiles = {p.profile_id: p for p in problem.device_profiles}
    carriers = {
        item.carrier_id: item
        for collection in (
            problem.standalone_candidates,
            problem.shared_prep_candidates,
            problem.thermal_batch_candidates,
            problem.inventory_supply_candidates,
        )
        for item in collection
    }
    resources: dict[TaskId, tuple[ResourceUse, ...]] = {}
    for assignment in plan.validated.candidate.assignments:
        carrier = carriers[assignment.carrier_id]
        for tid in assignment.task_ids:
            phases = tuple(p.resource_use for p in carrier.resource_phases if p.task_id == tid)
            resources[tid] = phases or assignment.resource_uses
    tasks = {task.task_id: task for task in problem.logical_tasks}
    for record in problem.fixed_executions:
        for tid, interval in execution_intervals(record, problem.runtime).items():
            if tid in tasks:
                resources[tid] = execution_resource_uses(
                    record, interval, tasks[tid].operation.resource_requirements, problem.resources
                )
    labels = []
    for task in problem.logical_tasks:
        if task.task_id not in task_ids:
            continue
        parameters = []
        for resource in resources.get(task.task_id, task.operation.resource_requirements):
            if resource.resource_type != "DEVICE":
                continue
            profile = next(
                (profiles[ref] for ref in resource.profile_options if ref in profiles), None
            )
            physical = resource.physical_resource_id or resource.resource_id
            parameters.append(
                f"{profile.device_type} / {profile.mode} / {physical}" if profile else physical
            )
            parameters.extend(f"{item.parameter}={item.value}" for item in resource.configuration)
            if resource.effective_layer_indices:
                layers = "、".join(str(layer) for layer in resource.effective_layer_indices)
                parameters.append(f"第{layers}层")
        suffix = "；设备参数：" + "，".join(dict.fromkeys(parameters)) if parameters else ""
        description = task.operation.description or "按菜谱操作"
        labels.append(f"{names[task.recipe_instance_id]}：{description}{suffix}")
    prefix = "请开始：" if kind == "START" else "已到预计结束时间，请确认是否完成："
    return prefix + "；".join(dict.fromkeys(labels))
