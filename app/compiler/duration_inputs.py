"""缓冲只限制未开始菜谱的根节点；原操作、事实、强制程序均不改写。"""

from app.domain.candidates import InstantiationResult
from app.domain.duration_estimate import DurationBuffer, DurationProblemInputs, DurationRoot
from app.domain.inventory import committed_fulfillments
from app.domain.knowledge import MenuKnowledgeView
from app.domain.policy import SchedulingPolicy
from app.domain.runtime_snapshot import RuntimeSnapshot
from app.scheduling.duration_policy import DurationPolicy, source_estimates


def apply_duration_buffers(
    instantiated: InstantiationResult,
    knowledge: MenuKnowledgeView,
    runtime: RuntimeSnapshot,
    policy: SchedulingPolicy,
) -> tuple[InstantiationResult, tuple[DurationBuffer, ...]]:
    incoming = {dependency.successor_id for dependency in instantiated.dependencies}
    occurred = {
        task
        for execution in runtime.executions
        if execution.status != "PENDING"
        for task in execution.task_ids
    }
    occurred.update(
        task for fulfillment in committed_fulfillments(runtime) for task in fulfillment.task_ids
    )
    started_instances = {
        task.recipe_instance_id for task in instantiated.tasks if task.task_id in occurred
    }
    recipe_ids = {instance.recipe_instance_id: instance.recipe_id for instance in instantiated.menu}
    inputs = DurationProblemInputs(
        knowledge=knowledge,
        policy=policy,
        now_offset_sec=runtime.now_offset_sec,
        roots=tuple(
            DurationRoot(
                recipe_instance_id=task.recipe_instance_id,
                recipe_id=recipe_ids[task.recipe_instance_id],
                task_id=task.task_id,
                operation_id=task.operation_id.root,
                earliest_start_sec=task.earliest_start_sec,
                instance_already_started=task.recipe_instance_id in started_instances,
            )
            for task in instantiated.tasks
            if task.task_id not in incoming
        ),
    )
    adjusted = DurationPolicy().apply(inputs, source_estimates(inputs))
    not_before = {
        task: buffer.not_before_sec for buffer in adjusted.buffers for task in buffer.root_task_ids
    }
    return instantiated.model_copy(
        update={
            "tasks": tuple(
                task.model_copy(
                    update={
                        "earliest_start_sec": max(
                            task.earliest_start_sec, not_before.get(task.task_id, 0)
                        )
                    }
                )
                for task in instantiated.tasks
            )
        }
    ), adjusted.buffers
