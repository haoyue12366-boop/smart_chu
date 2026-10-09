"""仅按绑定当前整组及隔热装入边界的显式表生成预热替代备选。"""

import time

from app.domain.base import content_hash
from app.domain.candidates import SharedCandidateContext, stable_id
from app.domain.ids import CarrierId
from app.domain.scheduling_problem import CandidateCarrier, CarrierResourcePhase
from app.domain.thermal_profiles import thermal_profile
from app.domain.transition_rules import resolve_transition
from app.domain.transitions import (
    BatchTransitionBinding,
    ThermalState,
    TransitionPlan,
    TransitionRuleSpec,
    TransitionTarget,
)


def transition_variants(
    base: CandidateCarrier, context: SharedCandidateContext
) -> tuple[CandidateCarrier, ...]:
    knowledge = context.group.knowledge
    profile = thermal_profile(base.resource_uses[0], knowledge.devices, knowledge.profiles)
    if profile is None:
        return ()
    group_rule = next(r for r in knowledge.rules if r.rule_id == base.rule_refs[0])
    tasks = {t.task_id: t for t in context.instantiated.tasks}
    preheat = tuple(
        p for p in base.member_offsets if tasks[p.task_id].operation.action == "PREHEAT"
    )
    if not preheat or len({(p.start_offset_sec, p.end_offset_sec) for p in preheat}) != 1:
        return ()
    start, end = preheat[0].start_offset_sec, preheat[0].end_offset_sec
    results = []
    for rule in knowledge.rules:
        if time.monotonic_ns() >= context.deadline.expires_at_ns:
            raise TimeoutError("转换备选生成截止时间已到")
        if rule.kind != "TRANSITION":
            continue
        try:
            spec = TransitionRuleSpec.model_validate_json(rule.group_compatibility_predicate)
        except ValueError:
            continue
        if (
            spec.target_group_rule_id != group_rule.rule_id
            or spec.target_group_rule_hash != content_hash(group_rule)
            or spec.load_boundary != "ISOLATED_UNTIL_HEAT"
            or not spec.scope_note
            or spec.to_profile.profile_key != profile.profile_key
        ):
            continue
        hypothetical = ThermalState(
            physical_resource_id=profile.physical_resource_id,
            component_id=profile.component_id,
            condition=spec.from_condition,
            profile=spec.from_profile,
            at_sec=0,
            origin="PLANNED",
            source_ref="candidate-only-not-executed",
        )
        plan = resolve_transition(
            hypothetical,
            TransitionTarget(
                profile=profile,
                at_sec=0,
                rules=(rule,),
                release_kind=knowledge.release.release_kind,
                allow_delegated_estimates=context.group.allow_delegated_estimates,
            ),
            knowledge.release.rule_version,
        )
        if not plan.allowed or plan.duration_sec is None:
            continue
        plans: list[tuple[TransitionPlan, str | None]] = [(plan, None)]
        for device in context.group.runtime.device_states:
            observation = device.thermal_state
            if (
                observation is None
                or observation.condition != "TRANSITION_COMPLETED"
                or observation.origin != "OBSERVED"
                or observation.at_sec
                != context.group.runtime.time_origin.offset(device.observed_at)
                or observation.at_sec > context.group.runtime.now_offset_sec
                or (device.physical_resource_id, device.component_id)
                != (profile.physical_resource_id, profile.component_id)
            ):
                continue
            credited = resolve_transition(
                observation,
                TransitionTarget(
                    profile=profile,
                    at_sec=context.group.runtime.now_offset_sec,
                    rules=(rule,),
                    release_kind=knowledge.release.release_kind,
                    allow_delegated_estimates=context.group.allow_delegated_estimates,
                ),
                knowledge.release.rule_version,
            )
            if credited.allowed and credited.kind == "ALREADY_COMPLETED":
                plans.append((credited, observation.source_ref))
        for plan, completed_ref in plans:
            assert plan.duration_sec is not None
            delta = plan.duration_sec - (end - start)
            preheat_ids = tuple(p.task_id for p in preheat)
            ports = tuple(
                p.model_copy(
                    update={
                        "start_offset_sec": p.start_offset_sec
                        + (delta if p.start_offset_sec >= end else 0),
                        "end_offset_sec": p.end_offset_sec
                        + (delta if p.end_offset_sec >= end else 0),
                    }
                )
                for p in base.member_offsets
            )
            phases = [
                p.model_copy(
                    update={
                        "start_offset_sec": p.start_offset_sec
                        + (delta if p.start_offset_sec >= end else 0),
                        "end_offset_sec": p.end_offset_sec
                        + (delta if p.end_offset_sec > end else 0),
                    }
                )
                for p in base.resource_phases
            ]
            human = next(
                p.resource_use
                for p in base.resource_phases
                if p.resource_use.resource_type == "HUMAN"
            )
            phases.extend(
                CarrierResourcePhase(
                    task_id=preheat_ids[0],
                    start_offset_sec=start + p.start_offset_sec,
                    end_offset_sec=start + p.end_offset_sec,
                    resource_use=human.model_copy(update={"evidence_refs": rule.evidence_refs}),
                )
                for p in plan.human_phases
            )
            phases.sort(key=lambda p: (p.start_offset_sec, p.end_offset_sec, p.task_id.root))
            candidate = CandidateCarrier.model_validate(
                base.model_copy(
                    update={
                        "carrier_id": CarrierId("pending-transition"),
                        "duration_sec": base.duration_sec + delta,
                        "member_offsets": ports,
                        "resource_phases": tuple(phases),
                        "transition_binding": BatchTransitionBinding(
                            completed_state_ref=completed_ref,
                            rule_id=rule.rule_id,
                            source_preheat_sec=end - start,
                            transition_offset_sec=start,
                            preheat_task_ids=preheat_ids,
                        ),
                        "provenance_refs": tuple(
                            dict.fromkeys((*base.provenance_refs, *rule.evidence_refs))
                        ),
                    }
                ).model_dump()
            )
            results.append(
                candidate.model_copy(
                    update={
                        "carrier_id": CarrierId(
                            stable_id("thermal-transition", content_hash(candidate))
                        )
                    }
                )
            )
    return tuple(results)
