"""从发布固定程序重新计算热暴露及中途介入，不依赖 IR 展开结果。"""

from app.validation.schedule_context import Scan


def check_thermal_programs(scan: Scan) -> None:
    contexts = {c.recipe_id: c for c in scan.knowledge.recipe_contexts}
    for instance in scan.problem.recipe_instances:
        context = contexts.get(instance.recipe_id)
        if context is None:
            continue
        by_op = {
            t.operation_id: t.task_id
            for t in scan.problem.logical_tasks
            if t.recipe_instance_id == instance.recipe_instance_id
        }
        for program in context.program_constraints:
            groups = (program.before_group, program.intervention_group, program.after_group)
            if any(
                o not in by_op or by_op[o] not in scan.intervals for group in groups for o in group
            ):
                scan.fail("FIXED_PROGRAM", "固定程序缺少成员", program.program_id)
                continue
            before = [
                by_op[o] for o in program.before_group if scan.operations[by_op[o]].action == "HEAT"
            ]
            intervention = [by_op[o] for o in program.intervention_group]
            after = [
                by_op[o] for o in program.after_group if scan.operations[by_op[o]].action == "HEAT"
            ]
            elapsed = [
                sum(scan.intervals[t].end_sec - scan.intervals[t].start_sec for t in group)
                for group in (before, intervention, after)
            ]
            if (
                elapsed
                != [
                    program.before_intervention_sec,
                    program.intervention_sec,
                    program.remaining_sec,
                ]
                or elapsed[0] + elapsed[2] != program.active_process_sec
            ):
                scan.fail("THERMAL_EXPOSURE", "热暴露或介入时长偏离固定工艺", program.program_id)
            chain = before + intervention + after
            if any(
                scan.intervals[a].end_sec != scan.intervals[b].start_sec
                for a, b in zip(chain, chain[1:], strict=False)
            ):
                scan.fail("FIXED_PROGRAM", "固定程序在介入边界等待或提前操作", program.program_id)
            if not program.timer_paused:
                scan.fail("FIXED_PROGRAM", "未实现非暂停计时程序", program.program_id)
            for task in intervention:
                if not any(
                    u.resource_type == "HUMAN" for u in scan.operations[task].resource_requirements
                ):
                    scan.fail("FIXED_PROGRAM", "中途介入缺少真实人工", task.root)
        for operation_id, task_id in by_op.items():
            operation = scan.operations.get(task_id)
            if operation is None or task_id not in scan.intervals:
                continue
            for window in operation.execution_policy.interventions:
                other = by_op.get(window.operation_id)
                if other is None or other not in scan.intervals:
                    scan.fail("INTERVENTION", "必需介入未安排", operation_id.root)
                    continue
                delta = scan.intervals[other].start_sec - scan.intervals[task_id].start_sec
                if not window.offset_min_sec <= delta <= window.offset_max_sec:
                    scan.fail("INTERVENTION", "介入窗口不满足", operation_id.root)
