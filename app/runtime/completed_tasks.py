"""前置满足时刻：执行完成与有效库存替代共用同一判断。"""

from app.domain.advance_preparation import active_preparations
from app.domain.runtime_snapshot import RuntimeSnapshot


def completed_task_times(state: RuntimeSnapshot) -> dict[str, int]:
    completed = {
        span.task_id.root: span.interval.end_sec
        for execution in state.executions
        for span in execution.task_spans
        if execution.status == "COMPLETED" or span.task_id in execution.completed_task_ids
    }
    if state.details is not None:
        completed.update(
            {
                task.root: item.available_at_sec
                for item in active_preparations(state)
                for task in item.task_ids
            }
        )
        completed.update(
            {
                task.root: item.satisfied_at_sec
                for item in state.details.inventory_fulfillments
                if item.status == "COMMITTED"
                or (item.status == "PLANNED" and item.plan_version == state.current_plan_version)
                for task in item.task_ids
            }
        )
    return completed
