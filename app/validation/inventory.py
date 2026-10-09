"""从原菜谱、审核规则和实际产出独立审查库存替代；不调用 Compiler。"""

from collections import defaultdict
from fractions import Fraction

from app.domain.candidates import stable_id
from app.domain.quantity import ScaledQuantity, Unit
from app.domain.recovery import retained_input_tasks
from app.domain.scheduling_problem import CandidateCarrier
from app.validation.schedule_context import Scan


def check_inventory(scan: Scan, carrier: CandidateCarrier) -> None:
    try:
        _check_inventory(scan, carrier)
    except (ValueError, KeyError, StopIteration) as exc:
        scan.fail("INVENTORY_PROOF", "库存证据无法核对：" + str(exc), carrier.carrier_id.root)


def _check_inventory(scan: Scan, carrier: CandidateCarrier) -> None:
    details, binding = scan.runtime.details, carrier.inventory_supply
    if details is None or binding is None:
        scan.fail("INVENTORY_RULE", "库存供应缺少运行资格证明", carrier.carrier_id.root)
        return
    rule = next((rule for rule in details.inventory_rules if rule.rule_id == binding.rule_id), None)
    if (
        rule is None
        or rule.knowledge_version != scan.runtime.knowledge_version
        or not rule.evidence_refs
    ):
        scan.fail("INVENTORY_RULE", "库存规则缺失、版本不符或没有审核证据", carrier.carrier_id.root)
        return
    if rule.source_kind == "SYNTHETIC":
        if scan.runtime.execution_mode != "SIMULATED" or not all(
            ref.startswith("synthetic:") for ref in rule.evidence_refs
        ):
            scan.fail("INVENTORY_RULE", "合成库存依据不能用于真实执行", rule.rule_id)
    elif not any(
        item.rule_id == rule.rule_id
        and item.kind == "INVENTORY_SUBSTITUTION"
        and item.review_status == "APPROVED"
        and item.rule_version == scan.runtime.rule_version
        for item in scan.knowledge.rules
    ):
        scan.fail("INVENTORY_RULE", "固定知识没有开放此库存规则", rule.rule_id)
    covered = set(carrier.covers)
    tasks = {task.task_id: task for task in scan.problem.logical_tasks}
    if (
        not covered
        or covered != set(rule.target_task_ids)
        or not covered <= tasks.keys()
        or len(covered) != len(carrier.covers)
    ):
        scan.fail("INVENTORY_SCOPE", "替代范围与规则不同、重复或悬空", rule.rule_id)
        return
    if (
        carrier.duration_sec
        or carrier.resource_uses
        or carrier.resource_phases
        or carrier.rule_refs != (rule.rule_id,)
    ):
        scan.fail("INVENTORY_SCOPE", "库存供应伪造加工或没有绑定唯一规则", rule.rule_id)
    completed = {
        task
        for execution in scan.runtime.executions
        if execution.status == "COMPLETED"
        for task in execution.task_ids
    } | {task for item in scan.problem.fixed_supply_fulfillments for task in item.task_ids}
    if any(
        edge.successor_id in covered
        and edge.predecessor_id not in covered
        and edge.predecessor_id not in completed
        for edge in scan.problem.dependencies
    ):
        scan.fail("INVENTORY_SCOPE", "库存替代没有包含未完成的前置范围", rule.rule_id)
    instances = {tasks[task].recipe_instance_id for task in covered}
    if len(instances) != 1:
        scan.fail("INVENTORY_SCOPE", "库存规则串用菜谱实例", rule.rule_id)
        return
    instance_id = next(iter(instances))
    instance = next(
        item for item in scan.problem.recipe_instances if item.recipe_instance_id == instance_id
    )
    recipe = next(item for item in scan.knowledge.recipes if item.recipe_id == instance.recipe_id)
    internal_inputs = {
        item.spec_id for task in covered for item in scan.operations[task].material_inputs
    }
    outside_inputs = {
        item.spec_id
        for task in scan.problem.logical_tasks
        if task.recipe_instance_id == instance_id and task.task_id not in covered
        for item in scan.operations[task.task_id].material_inputs
    }
    outputs = [
        item
        for task in covered
        for item in scan.operations[task].material_outputs
        if item.spec_id not in internal_inputs or item.spec_id in outside_inputs
    ]
    if len(outputs) != 1 or outputs[0].quantity_kind != "EXACT" or outputs[0].quantity is None:
        scan.fail("INVENTORY_SCOPE", "替代范围仍有未满足分支或输出数量未知", rule.rule_id)
        return
    output = outputs[0]
    assert output.quantity is not None
    spec = next((item for item in recipe.material_specs if item.spec_id == output.spec_id), None)
    if (
        spec is None
        or binding.target_spec != spec
        or binding.target_supply_id != stable_id("material", instance_id.root, output.spec_id)
    ):
        scan.fail("INVENTORY_SPEC", "库存目标规格与原菜谱不一致", rule.rule_id)
        return
    expected = ScaledQuantity(value=0, scale=1, unit=rule.unit).add(output.quantity)
    if Fraction(
        expected.value, expected.scale
    ) != rule.quantity.fraction() or binding.quantity != ScaledQuantity(
        value=rule.quantity.numerator, scale=rule.quantity.denominator, unit=rule.unit
    ):
        scan.fail("INVENTORY_QUANTITY", "库存供给没有满足完整输出数量", rule.rule_id)
    expected_port = output.model_copy(update={"spec_id": binding.target_supply_id})
    if carrier.material_outputs != (expected_port,) or carrier.material_inputs:
        scan.fail("INVENTORY_SCOPE", "库存供应物料端口被改写", rule.rule_id)
    lots = {lot.lot_id: lot for lot in details.lots}
    total = Fraction(0)
    expiry = []
    if not binding.lot_claims or len({claim.lot_id for claim in binding.lot_claims}) != len(
        binding.lot_claims
    ):
        scan.fail("INVENTORY_QUANTITY", "库存批次为空或重复", rule.rule_id)
    for claim in binding.lot_claims:
        lot = lots.get(claim.lot_id)
        if lot is None:
            scan.fail("INVENTORY_STOCK", "库存候选引用不存在的实物", claim.lot_id)
            continue
        producer = next(
            (
                record
                for record in scan.runtime.executions
                if record.execution_id == lot.produced_by_execution_id
            ),
            None,
        )
        report = (
            next(
                (
                    item
                    for item in producer.produced
                    if item.lot_id.root == lot.lot_id and item.spec_id == lot.spec_id
                ),
                None,
            )
            if producer
            else None
        )
        if (
            producer is None
            or producer.status not in {"COMPLETED", "FAILED"}
            or report is None
            or report.quantity is None
            or lot.source_event_id not in {event.root for event in producer.event_refs}
            or not lot.availability_evidence
        ):
            scan.fail("INVENTORY_STOCK", "库存没有可追溯的实际形成证据", claim.lot_id)
        elif (
            report.quantity.model_copy(update={"value": 0})
            .add(lot.exact(lot.produced))
            .as_decimal()
            != report.quantity.as_decimal()
        ):
            scan.fail("INVENTORY_STOCK", "库存形成数量与执行报告不符", claim.lot_id)
        signature = {
            key: value
            for key, value in spec.model_dump().items()
            if key not in {"spec_id", "name", "provenance_refs", "review_status"}
        }
        source_signature = (
            {
                key: value
                for key, value in lot.material_spec.model_dump().items()
                if key not in {"spec_id", "name", "provenance_refs", "review_status"}
            }
            if lot.material_spec
            else None
        )
        if (
            lot.spec_id != rule.source_spec_id
            or lot.quality_status != "QUALIFIED"
            or lot.quantity_kind != "EXACT"
            or source_signature != signature
        ):
            scan.fail("INVENTORY_SPEC", "源批次规格、质量或数量资格不符", claim.lot_id)
        if lot.produced_at > scan.runtime.time_origin.at(scan.runtime.now_offset_sec) or (
            lot.expires_at
            and lot.expires_at <= scan.runtime.time_origin.at(scan.runtime.now_offset_sec)
        ):
            scan.fail("INVENTORY_TIME", "物料尚未形成或已经失效", claim.lot_id)
        quantity = ScaledQuantity(value=0, scale=1, unit=rule.unit).add(claim.quantity)
        total += Fraction(quantity.value, quantity.scale)
        if claim.quantity.value <= 0 or claim.consumed.numerator:
            scan.fail("INVENTORY_QUANTITY", "候选数量非正或虚构已消费事实", claim.lot_id)
        deadlines = []
        if lot.expires_at:
            deadlines.append(scan.runtime.time_origin.offset(lot.expires_at))
        if rule.max_age_sec is not None:
            deadlines.append(scan.runtime.time_origin.offset(lot.produced_at) + rule.max_age_sec)
        if not deadlines or min(deadlines) <= scan.runtime.now_offset_sec:
            scan.fail("INVENTORY_TIME", "库存缺少有效期依据或已到使用期限", claim.lot_id)
        if deadlines:
            expiry.append(min(deadlines))
    if total != rule.quantity.fraction():
        scan.fail("INVENTORY_QUANTITY", "实际分配合计与替代数量不符", rule.rule_id)
    if binding.available_at_sec != scan.runtime.now_offset_sec or binding.expires_at_sec != (
        min(expiry) if expiry else None
    ):
        scan.fail("INVENTORY_TIME", "候选库存时间证据被修改", rule.rule_id)


def check_inventory_totals(scan: Scan) -> None:
    try:
        _check_inventory_totals(scan)
    except (ValueError, KeyError) as exc:
        scan.fail("INVENTORY_QUANTITY", "库存总量无法核对：" + str(exc))


def _check_inventory_totals(scan: Scan) -> None:
    details = scan.runtime.details
    if details is None:
        return
    candidates = {
        candidate.carrier_id: candidate for candidate in scan.problem.inventory_supply_candidates
    }
    selected = [
        candidates[item.carrier_id]
        for item in scan.candidate.assignments
        if item.carrier_id in candidates
    ]
    lots = {lot.lot_id: lot for lot in details.lots}
    spent: dict[str, Fraction] = defaultdict(Fraction)
    pooled: dict[str, Fraction] = defaultdict(Fraction)
    units: dict[str, Unit] = {}
    covered = {task for candidate in selected for task in candidate.covers}
    fixed = {task for record in scan.problem.fixed_executions for task in record.task_ids} | {
        task for item in scan.problem.fixed_supply_fulfillments for task in item.task_ids
    }
    fixed.update(retained_input_tasks(scan.runtime))
    for carrier in selected:
        binding = carrier.inventory_supply
        if binding is None:
            continue
        for claim in binding.lot_claims:
            lot = lots.get(claim.lot_id)
            if lot is None or lot.unit is None:
                continue
            quantity = ScaledQuantity(value=0, scale=1, unit=lot.unit).add(claim.quantity)
            spent[lot.lot_id] += Fraction(quantity.value, quantity.scale)
            unit = units.setdefault(lot.spec_id, lot.unit)
            quantity = ScaledQuantity(value=0, scale=1, unit=unit).add(claim.quantity)
            pooled[lot.spec_id] += Fraction(quantity.value, quantity.scale)
        if binding.expires_at_sec is not None:
            for demand in scan.problem.material_flow.demands:
                span = scan.intervals.get(demand.task_id)
                if (
                    demand.supply_id == binding.target_supply_id
                    and demand.task_id not in covered
                    and span
                    and span.start_sec >= binding.expires_at_sec
                ):
                    scan.fail("INVENTORY_TIME", "计划使用时库存已失效", demand.task_id.root)
    for identity, amount_spent in spent.items():
        if amount_spent > lots[identity].available.fraction() - lots[identity].reserved.fraction():
            scan.fail("INVENTORY_OVERCONSUMED", "多条库存候选重复承诺同一批余料", identity)
    supplies = {supply.supply_id: supply for supply in scan.problem.material_flow.supplies}
    for demand in scan.problem.material_flow.demands:
        if demand.supply_id in units and demand.task_id not in fixed | covered:
            requirement = supplies[demand.supply_id].requirement.quantity
            if requirement is not None:
                quantity = ScaledQuantity(value=0, scale=1, unit=units[demand.supply_id]).add(
                    requirement
                )
                pooled[demand.supply_id] += Fraction(quantity.value, quantity.scale) * Fraction(
                    demand.share_numerator, demand.share_denominator
                )
    for spec, amount in pooled.items():
        available = Fraction(0)
        for lot in details.lots:
            if lot.spec_id == spec and lot.quality_status == "QUALIFIED":
                left = ScaledQuantity(value=0, scale=1, unit=units[spec]).add(
                    lot.exact(lot.available)
                )
                reserved = ScaledQuantity(value=0, scale=1, unit=units[spec]).add(
                    lot.exact(lot.reserved)
                )
                available += Fraction(left.value, left.scale) - Fraction(
                    reserved.value, reserved.scale
                )
        if amount > available:
            scan.fail("INVENTORY_OVERCONSUMED", "库存替代侵占原菜单仍需的真实余量", spec)
