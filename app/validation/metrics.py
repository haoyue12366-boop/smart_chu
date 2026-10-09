"""独立扫描候选自报指标；不导入 scheduling 的计算实现。"""

from app.validation.schedule_context import Scan


def check_metrics(scan: Scan) -> None:
    claimed = scan.candidate.metrics
    if claimed is None:
        return
    if set(scan.intervals) != {t.task_id for t in scan.problem.logical_tasks}:
        scan.fail("METRICS", "不完整计划不能报告全桌指标")
        return
    human: list[tuple[int, int]] = []
    if scan.runtime.details is not None:
        fixed_ids = {execution.execution_id for execution in scan.problem.fixed_executions}
        for execution in scan.runtime.executions:
            if execution.execution_id in fixed_ids:
                continue
            explicit = (
                execution.scheduled_resource_spans
                if execution.status == "RUNNING"
                else execution.resource_spans
            )
            for span in explicit:
                if span.resource.resource_type == "HUMAN":
                    human.append((span.interval.start_sec, span.interval.end_sec))
    batches = {c.carrier_id: c for c in scan.problem.thermal_batch_candidates}
    for assignment in scan.candidate.assignments:
        batch = batches.get(assignment.carrier_id)
        if batch is not None:
            human.extend(
                (
                    assignment.interval.start_sec + p.start_offset_sec,
                    assignment.interval.start_sec + p.end_offset_sec,
                )
                for p in batch.resource_phases
                if p.resource_use.resource_type == "HUMAN"
            )
        if any(use.resource_type == "HUMAN" for use in assignment.resource_uses):
            human.append((assignment.interval.start_sec, assignment.interval.end_sec))
    for execution in scan.problem.fixed_executions:
        explicit = (
            execution.scheduled_resource_spans
            if execution.status == "RUNNING"
            else execution.resource_spans
        )
        if explicit:
            human.extend(
                (p.interval.start_sec, p.interval.end_sec)
                for p in explicit
                if p.resource.resource_type == "HUMAN"
            )
            continue
        if any(
            any(u.resource_type == "HUMAN" for u in scan.operations[t].resource_requirements)
            for t in execution.task_ids
            if t in scan.operations
        ):
            pairs = {
                (scan.intervals[t].start_sec, scan.intervals[t].end_sec)
                for t in execution.task_ids
                if t in scan.intervals
            }
            human.extend(pairs)
    spans = []
    block_start = block_end = None
    for start, end in sorted(human):
        if start == end:
            continue
        if block_end is None or start >= block_end + scan.problem.policy.objective.rest_gap_sec:
            if block_start is not None and block_end is not None:
                spans.append((block_start, block_end))
            block_start, block_end = start, end
        else:
            block_end = max(block_end, end)
    if block_start is not None and block_end is not None:
        spans.append((block_start, block_end))
    completions = []
    for instance in scan.problem.recipe_instances:
        ends = [
            scan.intervals[t.task_id].end_sec
            for t in scan.problem.logical_tasks
            if t.recipe_instance_id == instance.recipe_instance_id
        ]
        if not ends:
            scan.fail("METRICS", "缺少完整成菜依据")
            return
        completions.append(max(ends))
    if not completions and not (
        scan.runtime.details
        and scan.runtime.details.planning_kind == "REPLAN"
        and scan.runtime.details.cancelled_instance_ids
        and not scan.problem.logical_tasks
    ):
        scan.fail("METRICS", "空菜单缺少合法取消依据")
        return
    now = scan.runtime.now_offset_sec
    future = [end for end in completions if end > now]
    from app.domain.schedule import RecipeCookingFinish

    cooking = []
    for boundary in scan.problem.cooking_completions:
        if not all(task in scan.intervals for task in boundary.task_ids):
            scan.fail("METRICS", "出锅边界缺少实际工序端口")
            return
        cooking.append(
            RecipeCookingFinish(
                recipe_instance_id=boundary.recipe_instance_id,
                cooking_finish_sec=max(scan.intervals[task].end_sec for task in boundary.task_ids),
            )
        )
    cooking_ends = [item.cooking_finish_sec for item in cooking]
    future_cooking = [end for end in cooking_ends if end > now]
    complete_cooking = {item.recipe_instance_id for item in cooking} == {
        instance.recipe_instance_id for instance in scan.problem.recipe_instances
    } and (bool(cooking) or scan.problem.policy.objective.spread_basis == "COOKING_FINISH")
    expected: dict[str, object] = {
        "makespan_sec": max(completions, default=0),
        "completion_spread_sec": max(completions) - min(completions) if completions else 0,
        "cooking_finish_spread_sec": max(cooking_ends, default=0) - min(cooking_ends, default=0)
        if complete_cooking
        else None,
        "remaining_cooking_finish_spread_sec": max(future_cooking, default=0)
        - min(future_cooking, default=0)
        if complete_cooking
        else None,
        "recipe_cooking_finishes": tuple(cooking),
        "max_continuous_human_sec": max((end - start for start, end in spans), default=0),
        "total_human_work_sec": sum(end - start for start, end in human),
        "actual_human_work_sec": sum(max(0, min(end, now) - start) for start, end in human),
        "remaining_human_work_sec": sum(max(0, end - max(start, now)) for start, end in human),
        "remaining_makespan_sec": max(0, max(completions, default=0) - now),
        "remaining_max_continuous_human_sec": max(
            (end - start for start, end in spans if end > now), default=0
        ),
        "remaining_completion_spread_sec": max(future) - min(future) if future else 0,
        "serial_reference_sec": scan.problem.serial_reference.duration_sec
        if scan.problem.serial_reference
        else None,
    }
    if any(getattr(claimed, key) != value for key, value in expected.items()):
        scan.fail("METRICS", "候选自报指标与独立时间扫描不同")
    if claimed.disruption is not None:
        scan.fail("METRICS_CONTEXT", "旧计划未提供，不能验证候选自报的重排扰动")
