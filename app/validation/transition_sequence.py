"""独立按计划中真实腔体顺序核对转换来源、热状态及有效窗口。"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from app.domain.compatibility import GroupRuleSpec
from app.domain.thermal_profiles import thermal_profile
from app.domain.transitions import ThermalState, TransitionRuleSpec
from app.validation.schedule_context import Scan

if TYPE_CHECKING:
    from app.validation.resources import Occupancy


def check_transition_sequence(scan: Scan, occupancies: list[Occupancy]) -> None:
    candidates = {c.carrier_id: c for c in scan.problem.thermal_batch_candidates}
    selected = {
        frozenset(a.task_ids): candidates[a.carrier_id]
        for a in scan.candidate.assignments
        if a.carrier_id in candidates
    }
    if not any(c.transition_binding for c in selected.values()):
        return
    grouped: dict[tuple[str, str], list[Occupancy]] = defaultdict(list)
    for occurrence in occupancies:
        if occurrence.use.resource_type == "DEVICE":
            grouped[occurrence.key].append(occurrence)
    historical_tasks = {
        t for e in scan.runtime.executions if e.status == "COMPLETED" for t in e.task_ids
    }
    for key, nodes in grouped.items():
        observations = [
            d for d in scan.runtime.device_states if (d.physical_resource_id, d.component_id) == key
        ]
        previous = ThermalState(
            physical_resource_id=key[0],
            component_id=key[1],
            condition="UNKNOWN",
            at_sec=scan.runtime.now_offset_sec,
            origin="OBSERVED",
            source_ref="unknown-initial-state",
        )
        if len(observations) > 1:
            scan.fail("THERMAL_TRANSITION", "同物理设备有重复热观测")
        elif observations and observations[0].thermal_state is not None:
            observed = observations[0]
            thermal_observation = observed.thermal_state
            assert thermal_observation is not None
            previous = thermal_observation
            if (
                (previous.physical_resource_id, previous.component_id) != key
                or previous.origin != "OBSERVED"
                or previous.at_sec != scan.runtime.time_origin.offset(observed.observed_at)
                or previous.at_sec > scan.runtime.now_offset_sec
            ):
                scan.fail("THERMAL_TRANSITION", "初始热观测与实际设备、时刻不符")
        for occurrence in sorted(nodes, key=lambda n: (n.interval.start_sec, n.interval.end_sec)):
            if (
                previous.origin == "OBSERVED"
                and occurrence.interval.end_sec <= previous.at_sec
                and occurrence.tasks
                and set(occurrence.tasks) <= historical_tasks
            ):
                continue
            carrier = selected.get(frozenset(occurrence.tasks))
            profile = thermal_profile(
                occurrence.use, scan.knowledge.devices, scan.knowledge.profiles
            )
            if carrier is not None and carrier.transition_binding is not None:
                binding = carrier.transition_binding
                rule = next((r for r in scan.knowledge.rules if r.rule_id == binding.rule_id), None)
                try:
                    spec = (
                        TransitionRuleSpec.model_validate_json(rule.group_compatibility_predicate)
                        if rule
                        else None
                    )
                except ValueError:
                    spec = None
                at = occurrence.interval.start_sec + binding.transition_offset_sec
                good = spec is not None and profile is not None
                if spec is not None and profile is not None:
                    good = (
                        spec.to_profile.profile_key == profile.profile_key
                        and 0 <= at - previous.at_sec <= spec.max_idle_sec
                    )
                    good = good and (
                        previous.valid_until_sec is None or at <= previous.valid_until_sec
                    )
                    if previous.condition == "TRANSITION_COMPLETED":
                        good = (
                            good
                            and binding.completed_state_ref == previous.source_ref
                            and previous.origin == "OBSERVED"
                            and previous.completed_rule_id == binding.rule_id
                            and previous.completed_rule_version == scan.problem.rule_version
                            and previous.profile is not None
                            and previous.profile.profile_key == spec.to_profile.profile_key
                        )
                    else:
                        good = (
                            good
                            and binding.completed_state_ref is None
                            and previous.condition == spec.from_condition
                        )
                        if spec.from_profile is not None:
                            good = (
                                good
                                and previous.profile is not None
                                and previous.profile.profile_key == spec.from_profile.profile_key
                            )
                if not good:
                    scan.fail(
                        "THERMAL_TRANSITION",
                        "实际直接前驱状态不满足所选转换表或已过期",
                        carrier.carrier_id.root,
                    )
            condition = "UNKNOWN"
            if carrier is not None and len(carrier.rule_refs) == 1:
                group_rule = next(
                    (r for r in scan.knowledge.rules if r.rule_id == carrier.rule_refs[0]), None
                )
                if group_rule is not None:
                    try:
                        group = GroupRuleSpec.model_validate_json(
                            group_rule.group_compatibility_predicate
                        )
                        if group.thermal_model == "COLD_LOAD_SETUP_PREHEAT_HEAT_STOP_UNLOAD":
                            condition = "OFF"
                    except ValueError:
                        pass
            previous = ThermalState(
                physical_resource_id=key[0],
                component_id=key[1],
                condition=condition,
                profile=profile,
                at_sec=occurrence.interval.end_sec,
                origin="PLANNED",
                source_ref="previous-actual-planned-batch",
            )
