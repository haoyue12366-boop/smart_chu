"""独立从发布规则和源工序核对共享切配；不调用候选生成器或RuleEngine。"""

from collections import Counter

from app.domain.base import content_hash
from app.domain.compatibility import GroupRuleSpec
from app.domain.scheduling_problem import CandidateCarrier
from app.validation.recovery import replaced_failures
from app.validation.resumption import resume_for_carrier
from app.validation.schedule_context import Scan


def check_shared_prep(scan: Scan, carrier: CandidateCarrier) -> bool:
    def reject(message: str) -> bool:
        scan.fail("SHARED_RULE", message, carrier.carrier_id.root)
        return False

    if not scan.problem.policy.shared_prep or len(carrier.covers) < 2:
        return reject("共享切配未启用或成员不足")
    if len(carrier.rule_refs) != 1:
        return reject("共享载体必须引用明确的整组规则")
    rule = next((r for r in scan.knowledge.rules if r.rule_id == carrier.rule_refs[0]), None)
    if rule is None or rule.kind != "SHARED_PREP" or rule.rule_version != scan.problem.rule_version:
        return reject("共享规则不存在、类型或版本错误")
    try:
        spec = GroupRuleSpec.model_validate_json(rule.group_compatibility_predicate)
    except ValueError:
        return reject("共享规则结构不可解释")
    if spec.authority == "HUMAN_REVIEWED":
        allowed = rule.review_status == "APPROVED"
    else:
        allowed = (
            scan.problem.policy.allow_delegated_shared_estimates
            and scan.knowledge.release.release_kind == "development"
            and rule.review_status == "NEEDS_REVIEW"
        )
    if not allowed or not rule.evidence_refs or carrier.provenance_refs != rule.evidence_refs:
        return reject("审核状态、开发许可或证据错误")
    tasks = {t.task_id: t for t in scan.problem.logical_tasks}
    instances = {i.recipe_instance_id: i.recipe_id for i in scan.problem.recipe_instances}
    recipes = {r.recipe_id: r for r in scan.knowledge.recipes}
    if any(t not in tasks or t not in scan.operations for t in carrier.covers):
        return reject("共享覆盖来源不存在")
    identities = [
        (instances[tasks[t].recipe_instance_id], tasks[t].operation_id) for t in carrier.covers
    ]
    bindings = {(b.recipe_id, b.operation_id): b for b in spec.bindings}
    if Counter(identities) != Counter(bindings.keys()):
        return reject("共享成员不是规则批准的完整数量范围")
    resume = resume_for_carrier(scan, carrier)
    original_duration = (
        resume.resumption.original_duration_sec
        if resume and resume.resumption
        else carrier.duration_sec
    )
    if (
        original_duration != spec.duration_sec
        or len({b.processing_spec for b in spec.bindings}) != 1
    ):
        return reject("共享时长或加工规格偏离规则")
    common = set(spec.bindings[0].configuration_keys)
    for b in spec.bindings[1:]:
        common.intersection_update(b.configuration_keys)
    if not common:
        return reject("共享整组无共同配置")
    actions = set()
    retried = replaced_failures(scan)
    fixed = {
        t
        for e in scan.runtime.executions
        if e.execution_id not in retried
        and (e.status not in {"PENDING", "READY"} or e.started_at is not None)
        for t in e.task_ids
    }
    required = []
    for tid, identity in zip(carrier.covers, identities, strict=True):
        op = scan.operations[tid]
        binding = bindings[identity]
        if binding.recipe_hash != recipes[
            identity[0]
        ].semantic_hash() or binding.operation_hash != content_hash(op):
            return reject("共享来源哈希已失效")
        if (
            tid in fixed
            or op.execution_policy.fixed_batch_id
            or op.execution_policy.interventions
            or op.execution_policy.batch_policy == "FIXED_RECIPE"
        ):
            return reject("共享覆盖已发生事实或固定工艺")
        actions.add(op.action)
        required.append(op.resource_requirements)
    if len(actions) != 1 or not actions <= {"CUT", "WASH"}:
        return reject("共享动作不兼容")
    # Independent ancestry walk; synchronizing ancestors would lose required process time.
    for tid in carrier.covers:
        pending = [d.successor_id for d in scan.problem.dependencies if d.predecessor_id == tid]
        seen = set()
        while pending:
            current = pending.pop()
            if current in carrier.covers:
                return reject("共享成员存在祖先后继")
            if current not in seen:
                seen.add(current)
                pending.extend(
                    d.successor_id for d in scan.problem.dependencies if d.predecessor_id == current
                )
    signatures = [tuple(u.model_dump(exclude={"evidence_refs"}) for u in row) for row in required]
    actual = tuple(u.model_dump(exclude={"evidence_refs"}) for u in carrier.resource_uses)
    if any(row != actual for row in signatures):
        return reject("共享实际资源偏离源成员要求")
    for i, use in enumerate(carrier.resource_uses):
        evidence = {ref for row in required for ref in row[i].evidence_refs}
        if set(use.evidence_refs) != evidence:
            return reject("共享资源来源证据缺失")
    return True
