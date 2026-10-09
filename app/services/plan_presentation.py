"""公开图形投影使用实际端口、物理日历及冻结事实，界面不再推断批次偏移。"""

from collections import Counter
from dataclasses import replace

from app.domain.candidates import stable_id
from app.domain.carrier_timing import task_intervals
from app.domain.ids import RecipeInstanceId
from app.domain.presentation import (
    AdvancePreparationPresentation,
    DisplayInterval,
    OperationPresentation,
    PlanPresentation,
    ResourcePresentation,
)
from app.domain.schedule import PublishedPlan
from app.domain.scheduling_problem import SchedulingProblem
from app.scheduling.calendar_projection import entries_for, fixed_ports
from app.scheduling.calendar_types import CalendarEntry

RESOURCE_LABELS = {
    "human_1": "人工",
    "stove_1": "灶眼一",
    "stove_2": "灶眼二",
    "oven_1": "烤箱",
    "steam_1": "蒸箱",
    "steam_oven_1": "蒸箱",
    "fridge_1": "冰箱",
    "hood_1": "烟机",
    "dishwasher_1": "洗碗机",
    "water_1": "咖啡饮水机",
}
COMPONENT_LABELS = {
    ("stove_1", "burner_1"): "灶眼一",
    ("stove_1", "burner_2"): "灶眼二",
    ("fridge_1", "cold_zone"): "冰箱 · 冷藏区",
    ("fridge_1", "freezer_zone"): "冰箱 · 冷冻区",
}


def _reuse_intervals(entries: tuple[CalendarEntry, ...]) -> tuple[tuple[DisplayInterval, ...], ...]:
    """标出已校验计划中兼容共享预约的真实交集，不把不同预约合为共同批次。"""
    spans: list[list[tuple[int, int]]] = [[] for _ in entries]
    groups: dict[tuple[str, str], list[int]] = {}
    for index, entry in enumerate(entries):
        if entry.use.conflict_policy in {"STATE_COMPATIBLE", "SHARED_AUXILIARY"}:
            groups.setdefault(entry.physical_key, []).append(index)
    for indices in groups.values():
        indices.sort(key=lambda index: entries[index].interval.start_sec)
        for position, index in enumerate(indices):
            entry = entries[index]
            for other_index in indices[position + 1 :]:
                other = entries[other_index]
                if other.interval.start_sec >= entry.interval.end_sec:
                    break
                if entry.task_ids and entry.task_ids == other.task_ids:
                    # 同一工序占两层只是两条投影，不是另一道菜参与复用。
                    continue
                if (
                    entry.reservation_id is not None
                    and entry.reservation_id == other.reservation_id
                ):
                    continue
                if entry.use.layer_index is not None or other.use.layer_index is not None:
                    left_config = {
                        item.parameter: item.value
                        for item in entry.use.configuration
                        if item.parameter != "duration_sec"
                    }
                    right_config = {
                        item.parameter: item.value
                        for item in other.use.configuration
                        if item.parameter != "duration_sec"
                    }
                    if (
                        entry.use.layer_index is None
                        or other.use.layer_index is None
                        or entry.use.layer_index == other.use.layer_index
                        or not {"temperature_c", "mode"} <= left_config.keys()
                        or left_config != right_config
                    ):
                        continue
                start = max(entry.interval.start_sec, other.interval.start_sec)
                end = min(entry.interval.end_sec, other.interval.end_sec)
                if start < end:
                    spans[index].append((start, end))
                    spans[other_index].append((start, end))
    merged_spans: list[tuple[DisplayInterval, ...]] = []
    for intersections in spans:
        merged: list[DisplayInterval] = []
        for start, end in sorted(set(intersections)):
            if merged and start <= merged[-1].end_sec:
                previous = merged.pop()
                merged.append(
                    DisplayInterval(
                        start_sec=previous.start_sec, end_sec=max(previous.end_sec, end)
                    )
                )
            else:
                merged.append(DisplayInterval(start_sec=start, end_sec=end))
        merged_spans.append(tuple(merged))
    return tuple(merged_spans)


def presentation(plan: PublishedPlan, problem: SchedulingProblem) -> PlanPresentation:
    if plan.validated.candidate.problem_hash != problem.problem_hash:
        raise ValueError("显示问题不属于该发布计划")
    tasks = {t.task_id: t for t in problem.logical_tasks}
    prepared_tasks = {task for item in problem.advance_preparations for task in item.task_ids}
    recipes = {r.recipe_instance_id: r for r in problem.recipe_instances}
    assignments = plan.validated.candidate.assignments
    spans = task_intervals(problem, assignments)
    spans.update((p.task_id, p.interval) for p in fixed_ports(problem))
    owners = {t: a.carrier_id.root for a in assignments for t in a.task_ids}
    supplied = {t for f in problem.fixed_supply_fulfillments for t in f.task_ids}
    for fact in problem.fixed_executions:
        owners.update(
            (t, fact.carrier_id or fact.execution_id.root) for t in fact.task_ids if t in tasks
        )
    groups: dict[str, set[RecipeInstanceId]] = {}
    for task, owner in owners.items():
        groups.setdefault(owner, set()).add(tasks[task].recipe_instance_id)
    operations = tuple(
        OperationPresentation(
            task_id=t.task_id.root,
            carrier_id=owners.get(t.task_id, "inventory"),
            recipe_id=recipes[t.recipe_instance_id].recipe_id.root,
            recipe_instance_id=t.recipe_instance_id.root,
            recipe_name=recipes[t.recipe_instance_id].name,
            title=t.operation.description or t.operation.action.value,
            action=t.operation.action.value,
            start_sec=spans[t.task_id].start_sec,
            end_sec=spans[t.task_id].end_sec,
            shared=len(groups.get(owners.get(t.task_id, ""), set())) > 1,
            frozen=t.task_id not in {tid for a in assignments for tid in a.task_ids},
            inventory_supplied=t.task_id in supplied,
        )
        for t in sorted(
            (task for task in problem.logical_tasks if task.task_id not in prepared_tasks),
            key=lambda t: (spans[t.task_id].start_sec, t.task_id.root),
        )
    )
    resources = []
    resource_lanes: dict[str, None] = {}
    devices = {device.device_instance_id: device for device in problem.resources}
    component_counts = Counter(
        physical_id for physical_id, _ in {device.competition_key for device in problem.resources}
    )
    entries = entries_for(problem, assignments, include_fixed=True)
    if prepared_tasks:
        entries = tuple(
            replace(
                entry,
                task_ids=tuple(task for task in entry.task_ids if task not in prepared_tasks),
            )
            for entry in entries
            if not entry.task_ids or any(task not in prepared_tasks for task in entry.task_ids)
        )
    entries = tuple(
        replace(
            entry,
            use=entry.use.model_copy(
                update={"occupied_layer_indices": (), "layer_index": layer, "units": 1}
            ),
        )
        if layer is not None
        else entry
        for entry in entries
        for layer in (entry.use.effective_layer_indices or (None,))
    )
    reuse_intervals = _reuse_intervals(entries)
    for index, entry in enumerate(entries):
        device_label = RESOURCE_LABELS.get(entry.physical_key[0], entry.physical_key[0])
        component_label = COMPONENT_LABELS.get(
            entry.physical_key,
            f"{device_label} · {entry.physical_key[1]}"
            if component_counts[entry.physical_key[0]] > 1
            else device_label,
        )
        layer_index = entry.use.layer_index
        resource_label = (
            f"{component_label} · 第{layer_index}层" if layer_index is not None else component_label
        )
        if layer_index is not None:
            device = devices[entry.use.resource_id]
            for layer in range(1, device.capacity + 1):
                resource_lanes.setdefault(f"{component_label} · 第{layer}层", None)
        else:
            resource_lanes.setdefault(resource_label, None)
        members = tuple(
            dict.fromkeys(tasks[t].recipe_instance_id for t in entry.task_ids if t in tasks)
        )
        resources.append(
            ResourcePresentation(
                entry_id=stable_id("display-resource", plan.publication_id, str(index)),
                resource_id=entry.physical_key[0],
                component_id=entry.physical_key[1],
                resource_label=resource_label,
                device_label=device_label,
                layer_index=layer_index,
                task_ids=tuple(t.root for t in entry.task_ids),
                recipe_instance_ids=tuple(r.root for r in members),
                recipe_names=tuple(recipes[r].name for r in members),
                title=" / ".join(
                    dict.fromkeys(
                        tasks[t].operation.description or tasks[t].operation.action.value
                        for t in entry.task_ids
                        if t in tasks
                    )
                ),
                start_sec=entry.interval.start_sec,
                end_sec=entry.interval.end_sec,
                shared=len(members) > 1,
                frozen=entry.frozen,
                configuration=entry.use.configuration,
                reuse_intervals=reuse_intervals[index],
            )
        )
    return PlanPresentation(
        time_origin=plan.time_origin,
        range_end_sec=max((x.end_sec for x in (*operations, *resources)), default=0),
        operations=operations,
        resources=tuple(resources),
        resource_lanes=tuple(resource_lanes),
        advance_preparations=tuple(
            AdvancePreparationPresentation(
                preparation_id=item.preparation_id,
                recipe_instance_id=item.recipe_instance_id.root,
                recipe_id=item.recipe_id.root,
                recipe_name=recipes[item.recipe_instance_id].name,
                task_ids=tuple(task.root for task in item.task_ids),
                description=item.description,
                original_duration_sec=item.original_duration_sec,
                source_kind=item.source_kind,
            )
            for item in problem.advance_preparations
        ),
    )
