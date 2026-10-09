"""按实例保留完整模板身份；实际完成与未执行需求分开。"""

from app.domain.advance_preparation import active_preparations
from app.domain.candidates import InstantiationResult, stable_id
from app.domain.errors import ErrorCode
from app.domain.ids import TaskId
from app.domain.inventory import committed_fulfillments
from app.domain.knowledge import MenuKnowledgeView
from app.domain.recovery import superseded_failures
from app.domain.reports import CompilationFailure
from app.domain.runtime_snapshot import RuntimeSnapshot
from app.domain.scheduling_problem import LogicalTask, RecipeInstance, TaskDependency


def failure(message: str, code: ErrorCode = ErrorCode.DATA_NOT_READY) -> CompilationFailure:
    return CompilationFailure(code=code, failure_class="INPUT_INVALID", message=message)


def instantiate(
    menu: tuple[RecipeInstance, ...], knowledge: MenuKnowledgeView, runtime: RuntimeSnapshot
) -> InstantiationResult | CompilationFailure:
    if (runtime.knowledge_version, runtime.rule_version, runtime.snapshot_id) != (
        knowledge.release.knowledge_version,
        knowledge.release.rule_version,
        knowledge.release.snapshot_id,
    ):
        return failure("运行状态与指定知识版本不一致", ErrorCode.STATE_CONFLICT)
    empty_after_cancel = (
        runtime.details is not None
        and runtime.details.planning_kind == "REPLAN"
        and bool(runtime.details.cancelled_instance_ids)
    )
    if (not menu and not empty_after_cancel) or len({i.recipe_instance_id for i in menu}) != len(
        menu
    ):
        return failure("菜单为空或实例身份重复", ErrorCode.INVALID_REQUEST)
    recipes = {r.recipe_id: r for r in knowledge.recipes}
    if len(recipes) != len(knowledge.recipes):
        return failure("知识菜谱身份重复")
    tasks: list[LogicalTask] = []
    dependencies: list[TaskDependency] = []
    for instance in menu:
        recipe = recipes.get(instance.recipe_id)
        if recipe is None:
            return failure("菜单含未知菜谱", ErrorCode.UNKNOWN_RECIPE)
        if recipe.name != instance.name:
            return failure("菜谱名称与ID不一致", ErrorCode.RECIPE_NAME_MISMATCH)
        ids = {
            o.operation_id: TaskId(
                stable_id("task", instance.recipe_instance_id.root, o.operation_id.root)
            )
            for o in recipe.operations
        }
        tasks.extend(
            LogicalTask(
                task_id=ids[o.operation_id],
                recipe_instance_id=instance.recipe_instance_id,
                operation_id=o.operation_id,
                operation=o,
                earliest_start_sec=runtime.now_offset_sec,
            )
            for o in recipe.operations
        )
        dependencies.extend(
            TaskDependency(
                predecessor_id=ids[d.predecessor_id],
                successor_id=ids[d.successor_id],
                min_lag_sec=d.min_lag_sec,
                max_lag_sec=d.max_lag_sec,
                evidence_refs=d.evidence_refs,
            )
            for d in recipe.dependencies
        )
    task_ids = {t.task_id for t in tasks}
    if runtime.details and set(runtime.details.blocked_task_ids) & task_ids:
        return failure("工序存在待审计的冲突反馈，派发已暂停", ErrorCode.STATE_INCOMPLETE)
    completed: list[TaskId] = [
        task for item in committed_fulfillments(runtime) for task in item.task_ids
    ]
    completed.extend(task for item in active_preparations(runtime) for task in item.task_ids)
    running: list[TaskId] = []
    seen: set[TaskId] = set(completed)
    if not seen <= task_ids or len(completed) != len(seen):
        return failure("库存满足记录引用重复或菜单外需求", ErrorCode.STATE_CONFLICT)
    retried = superseded_failures(runtime)
    retired = set(runtime.details.retired_task_ids) if runtime.details else set()
    for record in runtime.executions:
        if not set(record.task_ids) <= task_ids | retired:
            return failure("执行记录引用菜单外任务", ErrorCode.STATE_CONFLICT)
        relevant_members = tuple(task for task in record.task_ids if task in task_ids)
        if not relevant_members:
            continue
        if record.status not in {"COMPLETED", "RUNNING"}:
            if record.status in {"FAILED", "CANCELLED"}:
                if record.execution_id in retried:
                    continue
                return failure("失败或取消工序需明确恢复路径", ErrorCode.STATE_INCOMPLETE)
            continue
        if seen.intersection(relevant_members):
            return failure("同一任务出现相互冲突的实际执行记录", ErrorCode.STATE_CONFLICT)
        if (
            record.started_at is None
            or runtime.time_origin.offset(record.started_at) > runtime.now_offset_sec
        ):
            return failure("执行事实开始晚于当前时刻", ErrorCode.STATE_CONFLICT)
        if (
            record.finished_at
            and runtime.time_origin.offset(record.finished_at) > runtime.now_offset_sec
        ):
            return failure("执行事实结束晚于当前时刻", ErrorCode.STATE_CONFLICT)
        seen.update(relevant_members)
        (completed if record.status == "COMPLETED" else running).extend(relevant_members)
    return InstantiationResult(
        menu=menu,
        tasks=tuple(tasks),
        dependencies=tuple(dependencies),
        completed_task_ids=tuple(completed),
        running_task_ids=tuple(running),
    )
