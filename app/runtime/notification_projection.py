"""从已验证的阶段时间生成操作与待确认提醒。"""

from typing import Literal

from app.domain.carrier_timing import task_intervals
from app.domain.ids import TaskId
from app.domain.runtime_session import NotificationRecord
from app.domain.runtime_snapshot import RuntimeSnapshot
from app.domain.schedule import PublishedPlan
from app.domain.scheduling_problem import SchedulingProblem


def notification_records(
    problem: SchedulingProblem | None, plan: PublishedPlan, runtime: RuntimeSnapshot
) -> tuple[NotificationRecord, ...]:
    spans = (
        task_intervals(problem, plan.validated.candidate.assignments)
        if problem
        else {task: a.interval for a in plan.validated.candidate.assignments for task in a.task_ids}
    )
    owners = {
        task: assignment.carrier_id.root
        for assignment in plan.validated.candidate.assignments
        for task in assignment.task_ids
    }
    for execution in runtime.executions:
        if execution.status == "RUNNING":
            spans.update((p.task_id, p.interval) for p in execution.task_spans)
            owners.update(
                (task, execution.carrier_id or execution.execution_id.root)
                for task in execution.task_ids
            )
    completed = {
        task
        for e in runtime.executions
        for task in (e.task_ids if e.status == "COMPLETED" else e.completed_task_ids)
    }
    if problem:
        selected = {item.carrier_id for item in plan.validated.candidate.assignments}
        completed.update(
            task
            for item in problem.inventory_supply_candidates
            if item.carrier_id in selected
            for task in item.covers
        )
    started = {task for e in runtime.executions for task in e.started_task_ids}
    groups: dict[tuple[int, int, str], list[TaskId]] = {}
    for task, span in spans.items():
        if task not in completed:
            groups.setdefault((span.start_sec, span.end_sec, owners[task]), []).append(task)
    labels: dict[TaskId, tuple[int, str]] = {}
    if problem:
        names = {r.recipe_instance_id: r.name for r in problem.recipe_instances}
        labels = {
            task.task_id: (
                index,
                f"{names[task.recipe_instance_id]}：{task.operation.description or '按菜谱操作'}",
            )
            for index, task in enumerate(problem.logical_tasks)
        }
    records = []
    for (start, end, _), task_list in sorted(groups.items()):
        tasks = tuple(sorted(task_list, key=lambda t: t.root))
        kinds: tuple[tuple[Literal["START", "EXPECTED_END"], int], ...] = (
            ("START", start),
            ("EXPECTED_END", end),
        )
        for kind, at in kinds:
            active_tasks = tuple(t for t in tasks if kind != "START" or t not in started)
            if not active_tasks:
                continue
            key = f"{plan.publication_id}:{active_tasks[0].root}:{kind}"
            label = "当前操作"
            if problem:
                label = (
                    "；".join(
                        dict.fromkeys(
                            text
                            for _, text in sorted(labels[t] for t in active_tasks if t in labels)
                        )
                    )
                    or label
                )
            prefix = "请开始：" if kind == "START" else "已到预计结束时间，请确认是否完成："
            text = prefix + label
            records.append(
                NotificationRecord(
                    notification_id=key,
                    session_id=plan.session_id.root,
                    plan_version=plan.plan_version,
                    deduplication_key=key,
                    trigger_at=runtime.time_origin.at(at),
                    text=text,
                    task_ids=active_tasks,
                    kind=kind,
                )
            )
    return tuple(records)
