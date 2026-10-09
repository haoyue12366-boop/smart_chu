"""未进入剩余任务覆盖的真实人工历史，仍属于整个会话。"""

from app.domain.runtime_facts import ActualResourceSpan
from app.domain.scheduling_problem import SchedulingProblem


def unmodeled_human_history(problem: SchedulingProblem) -> tuple[ActualResourceSpan, ...]:
    if problem.runtime.details is None:
        return ()
    fixed = {e.execution_id for e in problem.fixed_executions}
    return tuple(
        span
        for execution in problem.runtime.executions
        if execution.execution_id not in fixed
        for span in (
            execution.scheduled_resource_spans
            if execution.status == "RUNNING"
            else execution.resource_spans
        )
        if span.resource.resource_type == "HUMAN"
    )
