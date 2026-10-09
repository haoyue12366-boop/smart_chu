"""基于任务时间端口重新计算先后、时间网格和已发生事实。"""

from collections import Counter

from app.domain.advance_preparation import active_preparations
from app.domain.carrier_timing import member_offsets
from app.domain.ids import CarrierId, TaskId
from app.domain.inventory import committed_fulfillments
from app.domain.time import Interval
from app.validation.inventory import check_inventory
from app.validation.joint_thermal import check_joint_thermal
from app.validation.recovery import check_retry_assignment
from app.validation.resumption import check_resume_carrier, resume_for_carrier
from app.validation.schedule_context import Scan
from app.validation.shared_prep import check_shared_prep


def check_assignments(scan: Scan) -> None:
    problem, runtime = scan.problem, scan.runtime
    candidates = {
        c.carrier_id: c
        for c in (
            *problem.standalone_candidates,
            *problem.shared_prep_candidates,
            *problem.thermal_batch_candidates,
            *problem.inventory_supply_candidates,
        )
    }
    coverage: Counter[TaskId] = Counter()
    selected: Counter[CarrierId] = Counter()
    relevant = {t.task_id for t in problem.logical_tasks}
    fulfilled = committed_fulfillments(runtime)
    if problem.fixed_supply_fulfillments != fulfilled:
        scan.fail("HISTORY", "库存满足记录与实际运行事实不同")
    for fulfillment in fulfilled:
        for task_id in fulfillment.task_ids:
            coverage[task_id] += 1
            scan.intervals[task_id] = Interval(
                start_sec=fulfillment.satisfied_at_sec, end_sec=fulfillment.satisfied_at_sec
            )
    if problem.advance_preparations != active_preparations(runtime):
        scan.fail("PREPARATION_SOURCE", "提前备料与菜单声明记录不同")
    for preparation in problem.advance_preparations:
        for task_id in preparation.task_ids:
            coverage[task_id] += 1
            scan.intervals[task_id] = Interval(
                start_sec=preparation.available_at_sec, end_sec=preparation.available_at_sec
            )
    fixed = tuple(
        e
        for e in runtime.executions
        if e.status in {"COMPLETED", "RUNNING"} and set(e.task_ids).intersection(relevant)
    )
    if problem.fixed_executions != fixed:
        scan.fail("HISTORY", "IR 冻结事实与运行快照不同")
    for execution in fixed:
        if execution.started_at is None:
            scan.fail("HISTORY", "执行缺少实际开始", execution.execution_id.root)
            continue
        start = runtime.time_origin.offset(execution.started_at)
        end = (
            runtime.time_origin.offset(execution.finished_at)
            if execution.finished_at
            else runtime.now_offset_sec + (execution.remaining_sec or 0)
        )
        if (
            start < 0
            or start > runtime.now_offset_sec
            or end < start
            or (execution.status == "COMPLETED" and end > runtime.now_offset_sec)
            or (
                execution.status == "RUNNING"
                and (execution.remaining_sec is None or not execution.remaining_source_ref)
            )
        ):
            scan.fail("HISTORY", "实际时间或剩余时长缺少依据", execution.execution_id.root)
            continue
        for historical_task_id in execution.task_ids:
            if historical_task_id not in relevant:
                continue
            coverage[historical_task_id] += 1
            recorded = next(
                (p.interval for p in execution.task_spans if p.task_id == historical_task_id), None
            )
            if execution.task_spans and (
                recorded is None
                or recorded.start_sec < start
                or (execution.status == "COMPLETED" and recorded.end_sec > end)
            ):
                scan.fail("HISTORY", "内部阶段缺失或越过真实执行边界", historical_task_id.root)
            scan.intervals[historical_task_id] = recorded or Interval(start_sec=start, end_sec=end)
    task_map = {t.task_id: t for t in problem.logical_tasks}
    for assignment in scan.candidate.assignments:
        selected[assignment.carrier_id] += 1
        carrier = candidates.get(assignment.carrier_id)
        if carrier is not None:
            check_retry_assignment(scan, carrier)
            check_resume_carrier(scan, carrier, assignment.interval.start_sec)
        if carrier is None:
            scan.fail("COVERAGE", "计划引用不存在的载体", assignment.carrier_id.root)
        elif carrier.covers != assignment.task_ids:
            scan.fail("COVERAGE", "载体覆盖与计划不同", assignment.carrier_id.root)
        elif carrier.kind == "SHARED_PREP":
            check_shared_prep(scan, carrier)
        elif carrier.kind == "THERMAL_BATCH":
            check_joint_thermal(scan, carrier)
        elif carrier.kind == "INVENTORY_SUPPLY":
            check_inventory(scan, carrier)
        elif carrier.kind != "STANDALONE" or len(carrier.covers) != 1:
            scan.fail("UNSUPPORTED_CARRIER", "尚未支持此载体", assignment.carrier_id.root)
        if carrier is not None and tuple(
            use.model_dump(exclude={"layer_index"}) for use in assignment.resource_uses
        ) != tuple(use.model_dump(exclude={"layer_index"}) for use in carrier.resource_uses):
            scan.fail("RESOURCE_IDENTITY", "计划资源与候选不同", assignment.carrier_id.root)
        start, end = assignment.interval.start_sec, assignment.interval.end_sec
        if (
            start < runtime.now_offset_sec
            or end > problem.horizon_sec
            or start % problem.policy.time_grid_sec
            or end % problem.policy.time_grid_sec
            or (problem.policy.minute_output_mode == "INTEGER" and (start % 60 or end % 60))
        ):
            scan.fail("TIME_DOMAIN", "未来动作违反时间域或输出网格", assignment.carrier_id.root)
        offsets = member_offsets(carrier) if carrier else {}
        anchor = start
        if carrier and end - start != carrier.duration_sec:
            scan.fail("DURATION", "区间与载体时长不同", carrier.carrier_id.root)
        for task_id in assignment.task_ids:
            coverage[task_id] += 1
            lo, hi = offsets.get(task_id, (0, end - anchor))
            start, task_end = anchor + lo, anchor + hi
            scan.intervals[task_id] = Interval(start_sec=start, end_sec=task_end)
            if (
                start % problem.policy.time_grid_sec
                or task_end % problem.policy.time_grid_sec
                or (
                    problem.policy.minute_output_mode == "INTEGER" and (start % 60 or task_end % 60)
                )
            ):
                scan.fail("TIME_DOMAIN", "成员时间端口违反输出网格", task_id.root)
            operation = scan.operations.get(task_id)
            resume = resume_for_carrier(scan, carrier) if carrier else None
            duration = (
                resume.resumption.authorized_duration_sec
                if resume and resume.resumption
                else (operation.duration.execution_sec if operation else None)
            )
            task = task_map.get(task_id)
            if (
                operation
                and (
                    carrier is None
                    or carrier.kind not in {"SHARED_PREP", "THERMAL_BATCH", "INVENTORY_SUPPLY"}
                )
                and (duration is None or task_end - start != duration)
            ):
                scan.fail(
                    "DURATION",
                    "实际加工时长偏离发布工艺",
                    task_id.root,
                    evidence=operation.provenance_refs,
                )
            if task and (
                start < task.earliest_start_sec
                or (task.latest_end_sec is not None and task_end > task.latest_end_sec)
            ):
                scan.fail("TIME_DOMAIN", "任务违反编译时间域", task_id.root)
    if coverage != Counter({t.task_id: 1 for t in problem.logical_tasks}) or any(
        n != 1 for n in selected.values()
    ):
        scan.fail("COVERAGE", "每个任务必须由一次未来操作或真实执行恰好覆盖")


def check_dependencies(scan: Scan) -> None:
    selected = {item.carrier_id for item in scan.candidate.assignments}
    replaced = [
        set(item.covers)
        for item in scan.problem.inventory_supply_candidates
        if item.carrier_id in selected
    ]
    replaced.extend(set(item.task_ids) for item in scan.problem.fixed_supply_fulfillments)
    replaced.extend(set(item.task_ids) for item in scan.problem.advance_preparations)
    for dep in scan.problem.dependencies:
        if any({dep.predecessor_id, dep.successor_id} <= scope for scope in replaced):
            continue
        left, right = scan.intervals.get(dep.predecessor_id), scan.intervals.get(dep.successor_id)
        if left is None or right is None:
            continue
        lag = right.start_sec - left.end_sec
        if lag < dep.min_lag_sec or (dep.max_lag_sec is not None and lag > dep.max_lag_sec):
            scan.fail(
                "PRECEDENCE",
                "工序最小等待或最大间隔不满足",
                dep.predecessor_id.root,
                dep.successor_id.root,
                evidence=dep.evidence_refs,
            )
    for completion in scan.candidate.recipe_completions:
        tasks = [
            t.task_id
            for t in scan.problem.logical_tasks
            if t.recipe_instance_id == completion.recipe_instance_id
        ]
        ends = [scan.intervals[t].end_sec for t in tasks if t in scan.intervals]
        if not ends or completion.completion_sec != max(ends):
            scan.fail(
                "COMPLETION", "成菜完成必须包含全部必要收尾", completion.recipe_instance_id.root
            )
    if scan.candidate.recipe_completions and Counter(
        c.recipe_instance_id for c in scan.candidate.recipe_completions
    ) != Counter(i.recipe_instance_id for i in scan.problem.recipe_instances):
        scan.fail("COMPLETION", "成菜完成记录遗漏或重复")
