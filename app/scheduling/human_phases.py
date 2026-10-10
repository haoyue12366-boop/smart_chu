"""候选实际人工阶段规模，包含未选中备选，供目标预算检查。"""

from app.domain.ids import CarrierId
from app.domain.runtime_history import unmodeled_human_history
from app.domain.scheduling_problem import SchedulingProblem


def human_phase_count(
    problem: SchedulingProblem, *, selected_carriers: set[CarrierId] | None = None
) -> int:
    candidates = (
        *problem.standalone_candidates,
        *problem.shared_prep_candidates,
        *problem.thermal_batch_candidates,
    )
    count = sum(
        sum(u.resource_type == "HUMAN" for u in c.resource_uses)
        + sum(p.resource_use.resource_type == "HUMAN" for p in c.resource_phases)
        for c in candidates
        if selected_carriers is None or c.carrier_id in selected_carriers
    )
    tasks = {t.task_id: t for t in problem.logical_tasks}
    for fact in problem.fixed_executions:
        explicit = (
            fact.scheduled_resource_spans if fact.status == "RUNNING" else fact.resource_spans
        )
        if explicit:
            count += sum(span.resource.resource_type == "HUMAN" for span in explicit)
        else:
            count += sum(
                sum(u.resource_type == "HUMAN" for u in tasks[t].operation.resource_requirements)
                for t in fact.task_ids
                if t in tasks
            )
    return count + len(unmodeled_human_history(problem))
