"""根据明确前驱状态查询版本化转换表，不根据温差推算时长。"""

from app.domain.base import content_hash
from app.domain.processing_rules import ProcessingRule
from app.domain.transitions import (
    ThermalState,
    TransitionPlan,
    TransitionRuleSpec,
    TransitionTarget,
)


def resolve_transition(
    previous_state: ThermalState, next_profile: TransitionTarget, rule_version: str
) -> TransitionPlan:
    def reject(reason: str) -> TransitionPlan:
        return TransitionPlan(
            allowed=False,
            kind="REJECTED",
            duration_sec=None,
            source_state_hash=content_hash(previous_state),
            target_profile_key=next_profile.profile.profile_key,
            effective_at_sec=next_profile.at_sec,
            rejection_reason=reason,
        )

    if (previous_state.physical_resource_id, previous_state.component_id) != (
        next_profile.profile.physical_resource_id,
        next_profile.profile.component_id,
    ):
        return reject("前驱状态不属于目标物理设备")
    elapsed = next_profile.at_sec - previous_state.at_sec
    if elapsed < 0 or (
        previous_state.valid_until_sec is not None
        and next_profile.at_sec > previous_state.valid_until_sec
    ):
        return reject("前驱状态尚未发生或已超出明确有效期")
    completed = previous_state.condition == "TRANSITION_COMPLETED"
    if completed and (
        previous_state.origin != "OBSERVED" or previous_state.completed_rule_version != rule_version
    ):
        return reject("计划或错误版本不能充当已发生的转换事实")
    matches: list[tuple[ProcessingRule, TransitionRuleSpec]] = []
    for rule in next_profile.rules:
        if rule.kind != "TRANSITION" or rule.rule_version != rule_version:
            continue
        try:
            spec = TransitionRuleSpec.model_validate_json(rule.group_compatibility_predicate)
        except ValueError:
            continue
        permitted = (
            (rule.review_status == "APPROVED")
            if spec.authority == "HUMAN_REVIEWED"
            else (
                next_profile.allow_delegated_estimates
                and next_profile.release_kind == "development"
                and rule.review_status == "NEEDS_REVIEW"
            )
        )
        if (
            not permitted
            or not rule.evidence_refs
            or spec.authorization_ref not in rule.evidence_refs
        ):
            continue
        if (
            spec.to_profile.profile_key != next_profile.profile.profile_key
            or elapsed > spec.max_idle_sec
        ):
            continue
        if completed:
            if (
                rule.rule_id != previous_state.completed_rule_id
                or previous_state.profile is None
                or previous_state.profile.profile_key != spec.to_profile.profile_key
            ):
                continue
        elif spec.from_condition != previous_state.condition:
            continue
        elif spec.from_profile is not None and (
            previous_state.profile is None
            or spec.from_profile.profile_key != previous_state.profile.profile_key
        ):
            continue
        # A cold reset may ignore old settings, but never ignore a different physical device.
        if previous_state.profile is not None and (
            previous_state.profile.physical_resource_id,
            previous_state.profile.component_id,
        ) != (next_profile.profile.physical_resource_id, next_profile.profile.component_id):
            continue
        duration = rule.transition_duration_sec
        if duration is None:
            continue
        phases = sorted(spec.human_phases, key=lambda p: p.start_offset_sec)
        if any(p.end_offset_sec > duration for p in phases) or any(
            a.end_offset_sec > b.start_offset_sec for a, b in zip(phases, phases[1:], strict=False)
        ):
            continue
        matches.append((rule, spec))
    if len(matches) != 1:
        return reject("没有唯一匹配且许可完整的状态转换表项")
    rule, spec = matches[0]
    kind = (
        "ALREADY_COMPLETED"
        if completed
        else "COLD_START"
        if previous_state.condition in {"OFF", "COLD"}
        else "EXPLICIT_RESET"
        if previous_state.condition == "UNKNOWN"
        else "REUSE"
        if rule.transition_duration_sec == 0
        else "CONFIGURATION_CHANGE"
    )
    return TransitionPlan(
        allowed=True,
        kind=kind,
        duration_sec=0 if completed else rule.transition_duration_sec,
        human_phases=() if completed else spec.human_phases,
        rule_refs=(rule.rule_id,),
        evidence_refs=tuple(dict.fromkeys((*rule.evidence_refs, previous_state.source_ref))),
        source_state_hash=content_hash(previous_state),
        target_profile_key=next_profile.profile.profile_key,
        effective_at_sec=next_profile.at_sec,
    )
