"""原菜谱的固定批次按实例保留，不生成跨菜组合。"""

from collections import defaultdict

from app.domain.candidates import InstantiationResult, stable_id
from app.domain.ids import RecipeInstanceId
from app.domain.scheduling_problem import LogicalTask
from app.domain.thermal import FixedTaskBatch


def fixed_batches(instantiated: InstantiationResult) -> tuple[FixedTaskBatch, ...]:
    groups: dict[tuple[RecipeInstanceId, str], list[LogicalTask]] = defaultdict(list)
    for task in instantiated.tasks:
        policy = task.operation.execution_policy
        if policy.batch_policy != "FIXED_RECIPE":
            continue
        if not policy.fixed_batch_id:
            raise ValueError("强制批次缺少稳定身份")
        groups[(task.recipe_instance_id, policy.fixed_batch_id)].append(task)
    return tuple(
        FixedTaskBatch(
            batch_id=stable_id("batch", instance.root, batch_id),
            recipe_instance_id=instance,
            members=tuple(t.task_id for t in members),
            evidence_refs=tuple(
                dict.fromkeys(ref for t in members for ref in t.operation.provenance_refs)
            ),
        )
        for (instance, batch_id), members in groups.items()
    )
