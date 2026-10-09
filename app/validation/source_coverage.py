"""从已发布菜谱逐实例核对工序与依赖，防止 IR 漏项自证。"""

from collections import Counter

from app.validation.schedule_context import Scan


def check_source_coverage(scan: Scan) -> None:
    problem, knowledge = scan.problem, scan.knowledge
    if (
        scan.runtime != problem.runtime
        # 独立校验重新读取全部实际内容，不接受调用方的哈希缓存作为证明。
        or scan.candidate.problem_hash != scan.problem_hash
        or knowledge.release.knowledge_version != problem.knowledge_version
        or knowledge.release.rule_version != problem.rule_version
        or knowledge.release.release_kind != problem.knowledge_release_kind
        or knowledge.release.snapshot_id != problem.snapshot_id
        or knowledge.snapshot_schema_version != problem.snapshot_schema_version
    ):
        scan.fail("IDENTITY", "候选、状态与知识身份不一致")
    if (
        problem.resources != knowledge.devices
        or problem.device_profiles != knowledge.profiles
        or problem.transition_rules != knowledge.rules
    ):
        scan.fail("SOURCE_RESOURCE", "IR 设备或规则不等于发布事实")
    recipes = {r.recipe_id: r for r in knowledge.recipes}
    seen = set()
    expected_dependencies = []
    for instance in problem.recipe_instances:
        recipe = recipes.get(instance.recipe_id)
        if recipe is None or recipe.name != instance.name:
            scan.fail("SOURCE_COVERAGE", "菜单不属于当前知识", instance.recipe_instance_id.root)
            continue
        tasks = [
            t for t in problem.logical_tasks if t.recipe_instance_id == instance.recipe_instance_id
        ]
        if Counter(t.operation_id for t in tasks) != Counter(
            o.operation_id for o in recipe.operations
        ):
            scan.fail(
                "SOURCE_COVERAGE",
                "必需工艺在 IR 中缺失、重复或新增",
                instance.recipe_instance_id.root,
            )
        by_operation = {t.operation_id: t for t in tasks}
        for op in recipe.operations:
            task = by_operation.get(op.operation_id)
            if task is None:
                continue
            seen.add(task.task_id)
            scan.operations[task.task_id] = op
            # 同一严格契约直接比较全部 JSON 内容，保留数值类型并避免重复哈希。
            if task.operation.model_dump_json() != op.model_dump_json():
                scan.fail(
                    "SOURCE_OPERATION",
                    "IR 修改了发布工序",
                    task.task_id.root,
                    evidence=op.provenance_refs,
                )
        for dep in recipe.dependencies:
            if dep.predecessor_id in by_operation and dep.successor_id in by_operation:
                expected_dependencies.append(
                    (
                        by_operation[dep.predecessor_id].task_id,
                        by_operation[dep.successor_id].task_id,
                        dep.min_lag_sec,
                        dep.max_lag_sec,
                    )
                )
    actual = [
        (d.predecessor_id, d.successor_id, d.min_lag_sec, d.max_lag_sec)
        for d in problem.dependencies
    ]
    if problem.policy.critical_window_policy_id == "SERIAL_RECIPE_V1":
        # 独立从发布原图重算源点/汇点，不调用 Compiler 或策略边生成函数。
        for previous, following in zip(
            problem.recipe_instances, problem.recipe_instances[1:], strict=False
        ):
            left, right = recipes.get(previous.recipe_id), recipes.get(following.recipe_id)
            if left is None or right is None:
                continue
            outgoing = {d.predecessor_id for d in left.dependencies}
            incoming = {d.successor_id for d in right.dependencies}
            ends = {
                t.task_id
                for t in problem.logical_tasks
                if t.recipe_instance_id == previous.recipe_instance_id
                and t.operation_id not in outgoing
            }
            starts = {
                t.task_id
                for t in problem.logical_tasks
                if t.recipe_instance_id == following.recipe_instance_id
                and t.operation_id not in incoming
            }
            expected_dependencies.extend((end, start, 0, None) for end in ends for start in starts)
            for policy_dep in problem.dependencies:
                if policy_dep.predecessor_id in ends and policy_dep.successor_id in starts:
                    if policy_dep.evidence_refs != ("policy:SERIAL_RECIPE_V1",):
                        scan.fail("POLICY_EVIDENCE", "串行保护策略边的版本来源不符")
    if Counter(actual) != Counter(expected_dependencies):
        scan.fail("SOURCE_DEPENDENCY", "IR 依赖与发布菜谱不同")
    if seen != {t.task_id for t in problem.logical_tasks}:
        scan.fail("SOURCE_COVERAGE", "IR 包含菜单之外的任务")
    if not problem.recipe_instances and not (
        scan.runtime.details
        and scan.runtime.details.planning_kind == "REPLAN"
        and scan.runtime.details.cancelled_instance_ids
        and not problem.logical_tasks
    ):
        scan.fail("SOURCE_COVERAGE", "菜单为空")
    if scan.runtime.details is not None:
        for execution in scan.runtime.executions:
            if execution.status != "RUNNING":
                continue
            for span in execution.task_spans:
                confirmed_start = span.task_id in execution.started_task_ids
                finished = span.task_id in execution.completed_task_ids
                if (
                    confirmed_start
                    and not finished
                    and span.interval.end_sec <= scan.runtime.now_offset_sec
                ) or (
                    not confirmed_start and span.interval.start_sec < scan.runtime.now_offset_sec
                ):
                    scan.fail("HISTORY_PROGRESS", "阶段进度缺少当前实际确认", span.task_id.root)
