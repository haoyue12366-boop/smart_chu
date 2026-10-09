"""对插入后的完整设备日历复核转换，包含已经安排的后继；不移动旧预约。"""

from collections import defaultdict

from app.domain.scheduling_problem import SchedulingProblem
from app.domain.thermal_profiles import thermal_profile
from app.domain.thermal_sequence import initial_thermal_state, planned_exit_state
from app.domain.transition_rules import resolve_transition
from app.domain.transitions import TransitionTarget
from app.scheduling.calendar_types import CalendarEntry


def transition_rejections(
    problem: SchedulingProblem, entries: tuple[CalendarEntry, ...]
) -> tuple[str, ...]:
    carriers = {c.carrier_id: c for c in problem.thermal_batch_candidates}
    if not any(
        e.carrier_id in carriers and carriers[e.carrier_id].transition_binding is not None
        for e in entries
    ):
        return ()
    grouped: dict[tuple[str, str], list[CalendarEntry]] = defaultdict(list)
    for entry in entries:
        if entry.use.resource_type == "DEVICE":
            grouped[entry.physical_key].append(entry)
    failures = []
    for key, nodes in grouped.items():
        previous = initial_thermal_state(problem, key)
        for entry in sorted(nodes, key=lambda e: (e.interval.start_sec, e.interval.end_sec)):
            # Later observations supersede earlier completed occupancy; never overwrite
            # a future selected predecessor with an old initial observation.
            if (
                entry.frozen
                and entry.interval.end_sec <= previous.at_sec
                and previous.origin == "OBSERVED"
            ):
                continue
            carrier = carriers.get(entry.carrier_id) if entry.carrier_id is not None else None
            profile = thermal_profile(entry.use, problem.resources, problem.device_profiles)
            if carrier is not None and carrier.transition_binding is not None:
                binding = carrier.transition_binding
                rule = next(
                    (r for r in problem.transition_rules if r.rule_id == binding.rule_id), None
                )
                if rule is None or profile is None:
                    failures.append("转换缺少完整规则或配置")
                else:
                    plan = resolve_transition(
                        previous,
                        TransitionTarget(
                            profile=profile,
                            at_sec=entry.interval.start_sec + binding.transition_offset_sec,
                            rules=(rule,),
                            release_kind=problem.knowledge_release_kind,
                            allow_delegated_estimates=problem.policy.allow_delegated_shared_estimates,
                        ),
                        problem.rule_version,
                    )
                    ports = [
                        p for p in carrier.member_offsets if p.task_id in binding.preheat_task_ids
                    ]
                    if (
                        not plan.allowed
                        or any(
                            p.end_offset_sec - p.start_offset_sec != plan.duration_sec
                            for p in ports
                        )
                        or bool(binding.completed_state_ref) != (plan.kind == "ALREADY_COMPLETED")
                        or (
                            binding.completed_state_ref is not None
                            and binding.completed_state_ref != previous.source_ref
                        )
                    ):
                        failures.append("插入后的实际前驱不满足已安排批次转换条件")
            previous = planned_exit_state(
                problem,
                carrier,
                key,
                profile,
                entry.interval.end_sec,
                "planned-calendar-predecessor",
            )
    return tuple(failures)
