"""有来源的精确库存只替代规则覆盖的完整前处理闭合范围。"""

from fractions import Fraction

from app.domain.candidates import InstantiationResult, stable_id
from app.domain.ids import CarrierId
from app.domain.inventory import InventoryLotClaim, InventoryRule, InventorySupply
from app.domain.knowledge import MenuKnowledgeView
from app.domain.material import MaterialRequirement, MaterialSpec
from app.domain.quantity import ScaledQuantity
from app.domain.runtime_snapshot import RuntimeSnapshot
from app.domain.scheduling_problem import CandidateCarrier, LogicalTask
from app.domain.thermal import ExpandedPrograms


def _target(
    rule: InventoryRule, instantiated: InstantiationResult, knowledge: MenuKnowledgeView
) -> tuple[LogicalTask, MaterialRequirement, MaterialSpec]:
    tasks = {task.task_id: task for task in instantiated.tasks}
    covered = set(rule.target_task_ids)
    if not covered or len(covered) != len(rule.target_task_ids) or not covered <= tasks.keys():
        raise ValueError("库存规则替代范围为空、重复或引用菜单外需求")
    if covered & set((*instantiated.completed_task_ids, *instantiated.running_task_ids)):
        raise ValueError("库存不能重新替代已经开始或完成的需求")
    if len({tasks[task].recipe_instance_id for task in covered}) != 1:
        raise ValueError("一次库存替代须明确属于同一个菜谱实例")
    if any(
        edge.successor_id in covered
        and edge.predecessor_id not in covered
        and edge.predecessor_id not in instantiated.completed_task_ids
        for edge in instantiated.dependencies
    ):
        raise ValueError("库存替代遗漏仍需加工的前置范围")
    internal_inputs = {
        requirement.spec_id
        for task in covered
        for requirement in tasks[task].operation.material_inputs
    }
    outside_inputs = {
        requirement.spec_id
        for task in instantiated.tasks
        if task.task_id not in covered
        and task.recipe_instance_id == tasks[rule.target_task_ids[0]].recipe_instance_id
        for requirement in task.operation.material_inputs
    }
    boundary = [
        (tasks[task], requirement)
        for task in rule.target_task_ids
        for requirement in tasks[task].operation.material_outputs
        if requirement.spec_id not in internal_inputs or requirement.spec_id in outside_inputs
    ]
    if len(boundary) != 1:
        raise ValueError("规则没有完整覆盖所有对外输出，不能只跳过一条物料分支")
    task, output = boundary[0]
    if output.quantity_kind != "EXACT" or output.quantity is None or rule.quantity.numerator <= 0:
        raise ValueError("库存替代必须提供精确正数数量")
    expected = ScaledQuantity(value=0, unit=rule.unit, scale=1).add(output.quantity)
    if Fraction(expected.value, expected.scale) != rule.quantity.fraction():
        raise ValueError("库存数量与完整替代输出不一致")
    instance = next(
        item for item in instantiated.menu if item.recipe_instance_id == task.recipe_instance_id
    )
    recipe = next(item for item in knowledge.recipes if item.recipe_id == instance.recipe_id)
    spec = next(item for item in recipe.material_specs if item.spec_id == output.spec_id)
    return task, output, spec


def inventory_candidates(
    instantiated: InstantiationResult,
    knowledge: MenuKnowledgeView,
    runtime: RuntimeSnapshot,
    programs: ExpandedPrograms,
) -> tuple[tuple[CandidateCarrier, ...], tuple[str, ...]]:
    details = runtime.details
    if details is None:
        return (), ()
    candidates = []
    rejected = []
    completed = {
        execution.execution_id
        for execution in runtime.executions
        if execution.status == "COMPLETED"
    }
    committed = {
        task
        for item in details.inventory_fulfillments
        if item.status == "COMMITTED"
        for task in item.task_ids
    }
    for rule in details.inventory_rules:
        if set(rule.target_task_ids) <= committed:
            continue
        try:
            if rule.knowledge_version != runtime.knowledge_version or not rule.evidence_refs:
                raise ValueError("库存规则知识版本不符或缺少审核依据")
            if rule.source_kind == "SYNTHETIC":
                if runtime.execution_mode != "SIMULATED" or not all(
                    ref.startswith("synthetic:") for ref in rule.evidence_refs
                ):
                    raise ValueError("合成库存规则只能用于明确模拟")
            elif not any(
                source.rule_id == rule.rule_id
                and source.kind == "INVENTORY_SUBSTITUTION"
                and source.review_status == "APPROVED"
                and source.rule_version == runtime.rule_version
                for source in knowledge.rules
            ):
                raise ValueError("库存规则未在固定知识版本开放")
            task, output, spec = _target(rule, instantiated, knowledge)
            covered = set(rule.target_task_ids)
            if any(
                set(reservation.members) & covered and not set(reservation.members) <= covered
                for reservation in programs.reservations
            ):
                raise ValueError("库存替代不得截断菜谱强制设备阶段")
            signature = spec.model_dump(
                exclude={"spec_id", "name", "provenance_refs", "review_status"}
            )
            left = rule.quantity.fraction()
            claims = []
            expiry = []
            for lot in sorted(details.lots, key=lambda item: item.lot_id):
                if (
                    lot.spec_id != rule.source_spec_id
                    or lot.quantity_kind != "EXACT"
                    or lot.quality_status != "QUALIFIED"
                    or lot.produced_by_execution_id not in completed
                    or not lot.availability_evidence
                    or lot.material_spec is None
                ):
                    continue
                if lot.produced_at > runtime.time_origin.at(runtime.now_offset_sec) or (
                    lot.expires_at
                    and lot.expires_at <= runtime.time_origin.at(runtime.now_offset_sec)
                ):
                    continue
                if (
                    lot.material_spec.model_dump(
                        exclude={"spec_id", "name", "provenance_refs", "review_status"}
                    )
                    != signature
                ):
                    continue
                free = lot.available.fraction() - lot.reserved.fraction()
                deadlines = []
                if lot.expires_at is not None:
                    deadlines.append(runtime.time_origin.offset(lot.expires_at))
                if rule.max_age_sec is not None:
                    deadlines.append(runtime.time_origin.offset(lot.produced_at) + rule.max_age_sec)
                if not deadlines or min(deadlines) <= runtime.now_offset_sec:
                    continue
                if free <= 0:
                    continue
                quantity = (
                    ScaledQuantity(value=0, scale=1, unit=rule.unit)
                    .add(lot.exact(lot.available))
                    .add(ScaledQuantity(value=0, scale=1, unit=rule.unit))
                )
                reserved = ScaledQuantity(value=0, scale=1, unit=rule.unit).add(
                    lot.exact(lot.reserved)
                )
                take = min(
                    left,
                    Fraction(quantity.value, quantity.scale)
                    - Fraction(reserved.value, reserved.scale),
                )
                if take <= 0:
                    continue
                claims.append(
                    InventoryLotClaim(
                        lot_id=lot.lot_id,
                        quantity=ScaledQuantity(
                            value=take.numerator, scale=take.denominator, unit=rule.unit
                        ),
                    )
                )
                expiry.append(min(deadlines))
                left -= take
                if left == 0:
                    break
            if left:
                raise ValueError("实际余料不足或缺少规格、质量、时间及可用性证据")
            target_supply = stable_id("material", task.recipe_instance_id.root, output.spec_id)
            supply = InventorySupply(
                rule_id=rule.rule_id,
                target_supply_id=target_supply,
                target_spec=spec,
                quantity=ScaledQuantity(
                    value=rule.quantity.numerator, scale=rule.quantity.denominator, unit=rule.unit
                ),
                lot_claims=tuple(claims),
                available_at_sec=runtime.now_offset_sec,
                expires_at_sec=min(expiry) if expiry else None,
            )
            candidates.append(
                CandidateCarrier(
                    carrier_id=CarrierId(
                        stable_id(
                            "inventory",
                            rule.rule_id,
                            *[item.lot_id for item in claims],
                            str(runtime.state_revision),
                        )
                    ),
                    kind="INVENTORY_SUPPLY",
                    covers=rule.target_task_ids,
                    duration_sec=0,
                    rule_refs=(rule.rule_id,),
                    provenance_refs=rule.evidence_refs,
                    material_outputs=(output.model_copy(update={"spec_id": target_supply}),),
                    inventory_supply=supply,
                )
            )
        except (ValueError, StopIteration) as exc:
            rejected.append(f"{rule.rule_id}: {exc or '目标物料规格缺失'}")
    return tuple(candidates), tuple(rejected)
