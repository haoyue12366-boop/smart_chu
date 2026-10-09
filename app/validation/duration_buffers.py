"""从发布工艺和运行事实独立重算缓冲门槛，不调用策略或编译实现。"""

from app.domain.inventory import committed_fulfillments
from app.domain.time import align_up
from app.validation.schedule_context import Scan


def check_duration_buffers(scan: Scan) -> None:
    problem = scan.problem
    if problem.policy.duration_policy_id == "NOMINAL":
        if problem.duration_buffers:
            scan.fail("DURATION_POLICY", "NOMINAL 不允许隐藏缓冲")
        return
    recipes = {recipe.recipe_id: recipe for recipe in scan.knowledge.recipes}
    occurred = {
        task
        for record in scan.runtime.executions
        if record.status != "PENDING"
        for task in record.task_ids
    }
    occurred.update(task for item in committed_fulfillments(scan.runtime) for task in item.task_ids)
    started = {
        task.recipe_instance_id for task in problem.logical_tasks if task.task_id in occurred
    }
    buffers = {buffer.recipe_instance_id: buffer for buffer in problem.duration_buffers}
    if len(buffers) != len(problem.duration_buffers):
        scan.fail("DURATION_POLICY", "缓冲实例身份重复")
    expected_instances = set()
    earliest = dict(scan.runtime.details.earliest_starts) if scan.runtime.details else {}
    for instance in problem.recipe_instances:
        if instance.recipe_instance_id in started:
            continue
        recipe = recipes.get(instance.recipe_id)
        if recipe is None:
            continue
        # 固定加工及所有 HEAT 保护；可变操作只用于估算菜谱前的自由等待。
        estimates = tuple(
            operation
            for operation in recipe.operations
            if not operation.duration.fixed_process_time
            and operation.action != "HEAT"
            and (
                operation.action == "PREHEAT"
                or (
                    operation.action
                    in {"CUT", "MIX", "CLEAN", "LOAD", "UNLOAD", "TRANSFER", "PREPARE"}
                    and any(use.resource_type == "HUMAN" for use in operation.resource_requirements)
                )
            )
            and operation.duration.execution_sec
        )
        total = sum(operation.duration.execution_sec or 0 for operation in estimates)
        padding = align_up(
            (total * problem.policy.duration_buffer_basis_points + 9999) // 10000,
            problem.policy.time_grid_sec,
        )
        if not padding:
            continue
        expected_instances.add(instance.recipe_instance_id)
        buffer = buffers.get(instance.recipe_instance_id)
        incoming = {dependency.successor_id for dependency in recipe.dependencies}
        roots = {
            task.task_id
            for task in problem.logical_tasks
            if task.recipe_instance_id == instance.recipe_instance_id
            and task.operation_id not in incoming
        }
        base = max(scan.runtime.now_offset_sec, earliest.get(instance.recipe_instance_id.root, 0))
        if buffer is None or (
            buffer.recipe_id,
            set(buffer.root_task_ids),
            buffer.buffer_sec,
            buffer.base_start_sec,
            buffer.not_before_sec,
            buffer.estimate_refs,
            buffer.data_version,
            buffer.resource_reservations,
        ) != (
            instance.recipe_id,
            roots,
            padding,
            base,
            base + padding,
            tuple(
                f"{recipe.recipe_id.root}:{operation.operation_id.root}" for operation in estimates
            ),
            problem.policy.duration_data_version,
            (),
        ):
            scan.fail(
                "DURATION_POLICY",
                "缓冲记录与来源、策略或运行事实不一致",
                instance.recipe_instance_id.root,
            )
            continue
        for task in roots:
            if task in scan.intervals and scan.intervals[task].start_sec < base + padding:
                scan.fail("DURATION_POLICY", "未开始根工序忽略显式缓冲", task.root)
    if set(buffers) != expected_instances:
        scan.fail("DURATION_POLICY", "缓冲覆盖遗漏或改写已开始菜谱")
