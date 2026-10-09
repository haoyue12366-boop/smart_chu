"""独立核对热批次的转换表绑定；不调用Compiler或排程解释器。"""

from app.domain.base import content_hash
from app.domain.processing_rules import ProcessingRule
from app.domain.scheduling_problem import CandidateCarrier
from app.domain.thermal_profiles import thermal_profile
from app.domain.transitions import TransitionRuleSpec
from app.validation.schedule_context import Scan


def checked_transition(
    scan: Scan, carrier: CandidateCarrier, group_rule: ProcessingRule
) -> tuple[ProcessingRule, TransitionRuleSpec] | None:
    binding = carrier.transition_binding
    if binding is None:
        return None

    def reject(message: str) -> None:
        scan.fail("THERMAL_TRANSITION", message, carrier.carrier_id.root)

    rule = next((r for r in scan.knowledge.rules if r.rule_id == binding.rule_id), None)
    if rule is None or rule.kind != "TRANSITION" or rule.rule_version != scan.problem.rule_version:
        reject("转换规则身份或版本错误")
        return None
    try:
        spec = TransitionRuleSpec.model_validate_json(rule.group_compatibility_predicate)
    except ValueError:
        reject("转换规则结构无效")
        return None
    allowed = (
        rule.review_status == "APPROVED"
        if spec.authority == "HUMAN_REVIEWED"
        else (
            rule.review_status == "NEEDS_REVIEW"
            and scan.knowledge.release.release_kind == "development"
            and scan.problem.policy.allow_delegated_shared_estimates
        )
    )
    if not allowed or not rule.evidence_refs or spec.authorization_ref not in rule.evidence_refs:
        reject("转换审核或委托证据不足")
        return None
    profile = thermal_profile(
        carrier.resource_uses[0], scan.knowledge.devices, scan.knowledge.profiles
    )
    if (
        profile is None
        or spec.to_profile.profile_key != profile.profile_key
        or spec.target_group_rule_id != group_rule.rule_id
        or spec.target_group_rule_hash != content_hash(group_rule)
        or spec.load_boundary != "ISOLATED_UNTIL_HEAT"
        or not spec.scope_note
    ):
        reject("转换表未绑定完整热组、配置或隔热装入假设")
        return None
    duration = rule.transition_duration_sec
    phases = sorted(spec.human_phases, key=lambda p: p.start_offset_sec)
    if (
        duration is None
        or any(p.end_offset_sec > duration for p in phases)
        or any(
            a.end_offset_sec > b.start_offset_sec for a, b in zip(phases, phases[1:], strict=False)
        )
    ):
        reject("转换阶段越界或单人人工重叠")
        return None
    return rule, spec
