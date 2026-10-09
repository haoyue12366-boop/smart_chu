"""独立检查续做依据、保留实物和授权时窗，不接受编译器的物料豁免声明。"""

from collections import defaultdict
from datetime import timedelta
from fractions import Fraction

from app.domain.base import content_hash
from app.domain.candidates import stable_id
from app.domain.quantity import ScaledQuantity, Unit
from app.domain.recovery import RecoveryRuleSpec
from app.domain.runtime_snapshot import ExecutionRecord
from app.domain.scheduling_problem import CandidateCarrier
from app.validation.schedule_context import Scan


def check_resumptions(scan: Scan) -> None:
    records = {e.execution_id: e for e in scan.runtime.executions}
    for child in records.values():
        if child.recovery_kind != "RESUME" and child.resumption is None:
            continue
        parent = records.get(child.previous_execution_id) if child.previous_execution_id else None
        proof = child.resumption
        if parent is None or proof is None:
            scan.fail("RECOVERY_PROOF", "续做缺少失败父执行或完整工艺证明", child.execution_id.root)
            continue
        if (
            child.recovery_kind != "RESUME"
            or not child.recovery_rule_id
            or proof.authorization_event_id not in {e.root for e in child.event_refs}
            or parent.status != "FAILED"
            or parent.finished_at is None
            or proof.parent_hash != content_hash(parent)
            or parent.failure_output_status in {None, "WASTE"}
            or parent.produced
            or parent.completed_task_ids
            or not parent.consumed
            or proof.retained_inputs != parent.consumed
            or len({m.lot_id for m in proof.retained_inputs}) != len(proof.retained_inputs)
            or set(child.task_ids) != set(parent.task_ids)
            or len(child.task_ids) != len(set(child.task_ids))
            or child.consumed
        ):
            scan.fail("RECOVERY_PROOF", "续做保留投入或父执行证据不一致", child.execution_id.root)
        if parent.finished_at and (
            proof.authorized_at < parent.finished_at
            or proof.latest_start_at
            != parent.finished_at + timedelta(seconds=proof.procedure.max_pause_sec)
            or proof.authorized_at > proof.latest_start_at
            or (
                child.started_at is not None
                and not proof.authorized_at <= child.started_at <= proof.latest_start_at
            )
        ):
            scan.fail("RECOVERY_TIME", "续做授权或开始越过有效时间", child.execution_id.root)
        if child.started_at and child.status in {"RUNNING", "COMPLETED"}:
            finish = child.finished_at or scan.runtime.time_origin.at(
                scan.runtime.now_offset_sec + (child.remaining_sec or 0)
            )
            if finish < child.started_at + timedelta(seconds=proof.authorized_duration_sec):
                scan.fail("RECOVERY_TIME", "续做事实缩短了审核必需工艺", child.execution_id.root)
        if proof.source_kind == "SYNTHETIC":
            if (
                scan.runtime.execution_mode != "SIMULATED"
                or not proof.evidence_refs
                or not all(ref.startswith("synthetic:") for ref in proof.evidence_refs)
            ):
                scan.fail("RECOVERY_RULE", "合成工艺未限定在显式模拟中", child.execution_id.root)
        else:
            _published_rule(scan, child)
        _retained_quantities(scan, child)


def _published_rule(scan: Scan, child: ExecutionRecord) -> None:
    proof = child.resumption
    assert proof is not None
    rule = next((r for r in scan.knowledge.rules if r.rule_id == child.recovery_rule_id), None)
    if (
        rule is None
        or rule.kind != "RECOVERY"
        or rule.review_status != "APPROVED"
        or rule.rule_version != scan.runtime.rule_version
        or rule.evidence_refs != proof.evidence_refs
    ):
        scan.fail("RECOVERY_RULE", "续做规则不在固定知识已审核发布中", child.execution_id.root)
        return
    try:
        specification = RecoveryRuleSpec.model_validate_json(rule.group_compatibility_predicate)
    except ValueError:
        scan.fail("RECOVERY_RULE", "续做规则内容不可解释", child.execution_id.root)
        return
    tasks = {t.task_id: t for t in scan.problem.logical_tasks}
    instances = {i.recipe_instance_id: i.recipe_id for i in scan.problem.recipe_instances}
    recipes = {r.recipe_id: r for r in scan.knowledge.recipes}
    actual = {
        (instances[tasks[t].recipe_instance_id], tasks[t].operation_id): (
            recipes[instances[tasks[t].recipe_instance_id]].semantic_hash(),
            content_hash(scan.operations[t]),
        )
        for t in child.task_ids
        if t in tasks and t in scan.operations
    }
    expected = {
        (b.recipe_id, b.operation_id): (b.recipe_hash, b.operation_hash)
        for b in specification.bindings
    }
    if (
        specification.kind != "RESUME"
        or specification.remaining_sec != proof.authorized_duration_sec
        or specification.resume_procedure != proof.procedure
        or actual != expected
        or len(actual) != len(child.task_ids)
    ):
        scan.fail("RECOVERY_RULE", "续做偏离审核工艺或完整源内容", child.execution_id.root)


def _retained_quantities(scan: Scan, child: ExecutionRecord) -> None:
    proof = child.resumption
    assert proof is not None
    expected: dict[str, Fraction] = defaultdict(Fraction)
    units: dict[str, Unit | None] = {}
    tasks = {t.task_id: t for t in scan.problem.logical_tasks}
    for task_id in child.task_ids:
        operation, task = scan.operations.get(task_id), tasks.get(task_id)
        if operation is None or task is None:
            scan.fail("RECOVERY_MATERIAL", "续做成员缺少原工艺", task_id.root)
            continue
        for requirement in operation.material_inputs:
            key = stable_id("material", task.recipe_instance_id.root, requirement.spec_id)
            if requirement.quantity is None:
                # 定性份额须另有可核对的完整投入规则，不能自行推定为一整批。
                scan.fail("RECOVERY_MATERIAL", "当前续做路径要求可核对的精确数量", task_id.root)
                continue
            quantity = requirement.quantity
            units[key] = quantity.unit
            expected[key] += Fraction(quantity.value, quantity.scale)
        if operation.losses:
            scan.fail("RECOVERY_MATERIAL", "完整保留工艺不能忽略必需损耗", task_id.root)
    actual: dict[str, Fraction] = defaultdict(Fraction)
    for movement in proof.retained_inputs:
        unit = units.get(movement.spec_id)
        if movement.quantity is None or unit is None:
            scan.fail("RECOVERY_MATERIAL", "保留物料规格或数值量不匹配", child.execution_id.root)
            continue
        try:
            amount = ScaledQuantity(value=0, scale=1, unit=unit).add(movement.quantity)
            actual[movement.spec_id] += Fraction(amount.value, amount.scale)
        except ValueError:
            scan.fail("RECOVERY_MATERIAL", "保留物料单位不兼容", child.execution_id.root)
    if dict(actual) != dict(expected):
        scan.fail("RECOVERY_MATERIAL", "保留投入不满足完整原工艺数量", child.execution_id.root)


def resume_for_carrier(scan: Scan, carrier: CandidateCarrier) -> ExecutionRecord | None:
    return next(
        (e for e in scan.runtime.executions if e.execution_id == carrier.resume_execution_id), None
    )


def check_resume_carrier(scan: Scan, carrier: CandidateCarrier, start_sec: int) -> None:
    retry = resume_for_carrier(scan, carrier)
    relevant = [
        e
        for e in scan.runtime.executions
        if e.status == "PENDING"
        and e.recovery_kind == "RESUME"
        and set(e.task_ids).intersection(carrier.covers)
    ]
    if carrier.resume_execution_id is None and not relevant:
        return
    proof = retry.resumption if retry else None
    if (
        retry is None
        or proof is None
        or retry.status != "PENDING"
        or relevant != [retry]
        or set(carrier.covers) != set(retry.task_ids)
        or carrier.kind != proof.procedure.source_carrier_kind
        or carrier.resource_uses != proof.procedure.resource_uses
        or carrier.duration_sec != proof.authorized_duration_sec
        or carrier.resource_phases
        or carrier.member_offsets
    ):
        scan.fail("RECOVERY_CARRIER", "候选未严格实现已批准续做工艺", carrier.carrier_id.root)
        return
    start = scan.runtime.time_origin.at(start_sec)
    if not proof.authorized_at <= start <= proof.latest_start_at:
        scan.fail("RECOVERY_TIME", "续做候选越过允许的中断时窗", carrier.carrier_id.root)
