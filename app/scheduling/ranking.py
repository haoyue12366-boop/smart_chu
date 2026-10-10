"""所有完整候选按同一词典序比较；A 阶段先建立总耗时参考界。"""

from app.domain.schedule import CandidateSchedule
from app.domain.scheduling_problem import SchedulingProblem
from app.scheduling.metrics import compute_disruption, compute_metrics, objective_spread

CandidateRank = tuple[int, int, int, int, int, int, int, str]


def candidate_rank(
    candidate: CandidateSchedule,
    problem: SchedulingProblem,
    previous: CandidateSchedule | None = None,
    *,
    makespan_first: bool = False,
) -> CandidateRank:
    metrics = candidate.metrics or compute_metrics(candidate, problem)
    stages = problem.policy.objective.stages
    excess = max(0, objective_spread(metrics, problem) - problem.policy.objective.spread_target_sec)
    disruption = (
        compute_disruption(candidate, previous, problem)
        if previous and "STABILITY" in stages
        else None
    )
    quality_first = problem.policy.quality_first and not makespan_first
    if problem.policy.search_strategy == "FT_KITCHEN":
        return (
            metrics.makespan_sec,
            objective_spread(metrics, problem) if "SPREAD" in stages else 0,
            metrics.max_continuous_human_sec if "HUMAN_BUSY" in stages else 0,
            0,
            0,
            0,
            0,
            candidate.candidate_hash,
        )
    return (
        metrics.makespan_sec if makespan_first else excess if "SPREAD" in stages else 0,
        metrics.total_human_work_sec
        if quality_first and "TOTAL_HUMAN_WORK" in stages
        else metrics.max_continuous_human_sec
        if quality_first and "HUMAN_BUSY" in stages
        else metrics.makespan_sec,
        metrics.makespan_sec
        if quality_first
        else metrics.total_human_work_sec
        if "TOTAL_HUMAN_WORK" in stages
        else 0,
        metrics.max_continuous_human_sec if "HUMAN_BUSY" in stages else 0,
        disruption.time_shift_sec if disruption else 0,
        disruption.resource_changes if disruption else 0,
        disruption.group_changes if disruption else 0,
        candidate.candidate_hash,
    )
