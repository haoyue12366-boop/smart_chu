"""以来源供需重新计算份额、产出时间与实际库存，独立于 Compiler。"""

from collections import Counter, defaultdict
from fractions import Fraction

from app.domain.ids import TaskId
from app.domain.material import MaterialRequirement
from app.domain.quantity import ScaledQuantity
from app.domain.recovery import retained_input_tasks
from app.validation.inventory import check_inventory_totals
from app.validation.knowledge_materials import _share
from app.validation.material_values import RequirementValue, requirement_value
from app.validation.planned_materials import check_planned_allocations
from app.validation.running_inputs import check_running_materials
from app.validation.runtime_materials import check_actual_supply
from app.validation.schedule_context import Scan


def check_material_balance(scan: Scan) -> None:
    check_planned_allocations(scan)
    problem = scan.problem
    selected = {item.carrier_id for item in scan.candidate.assignments}
    supplied = {
        task
        for item in problem.inventory_supply_candidates
        if item.carrier_id in selected
        for task in item.covers
    }
    supplied.update(task for item in problem.fixed_supply_fulfillments for task in item.task_ids)
    supplied.update(task for item in problem.advance_preparations for task in item.task_ids)
    supplied.update(retained_input_tasks(scan.runtime))
    check_inventory_totals(scan)
    recipes = {r.recipe_id: r for r in scan.knowledge.recipes}
    supplies = {(s.recipe_instance_id, s.source_spec_id): s for s in problem.material_flow.supplies}
    if len(supplies) != len(problem.material_flow.supplies) or len(
        {s.supply_id for s in supplies.values()}
    ) != len(supplies):
        scan.fail("MATERIAL_BINDING", "供应身份重复或跨实例混用")
    expected_supply_keys = set()
    # 全字段不可变值作为扫描内的比较键；保留数量、身份和来源，避免为每次
    # 供需/候选端口比较重复 JSON 编码及 SHA-256。这不是跨请求证明缓存。
    expected_demands: Counter[tuple[TaskId, str, RequirementValue, Fraction, bool]] = Counter()
    consumed: dict[str, Fraction] = defaultdict(Fraction)
    fixed = {
        t: e
        for e in scan.runtime.executions
        if e.status in {"COMPLETED", "RUNNING"}
        for t in e.task_ids
    }
    for instance in problem.recipe_instances:
        recipe = recipes.get(instance.recipe_id)
        if recipe is None:
            continue
        task_ids = {
            t.operation_id: t.task_id
            for t in problem.logical_tasks
            if t.recipe_instance_id == instance.recipe_instance_id
        }
        source_supplies: dict[str, tuple[MaterialRequirement, TaskId | None]] = {
            m.spec_id: (m, None) for m in recipe.ingredient_requirements
        }
        for operation in recipe.operations:
            for output in operation.material_outputs:
                source_supplies[output.spec_id] = (output, task_ids.get(operation.operation_id))
        for spec, (requirement, producer_task) in source_supplies.items():
            key = (instance.recipe_instance_id, spec)
            expected_supply_keys.add(key)
            supply = supplies.get(key)
            if (
                supply is None
                or requirement_value(supply.requirement) != requirement_value(requirement)
                or supply.producer_task_id != producer_task
            ):
                scan.fail(
                    "MATERIAL_BINDING",
                    "IR 供应与来源物料不一致",
                    instance.recipe_instance_id.root,
                    spec,
                )
        for operation in recipe.operations:
            task = task_ids.get(operation.operation_id)
            if task is None:
                continue
            demands = [(r, False) for r in operation.material_inputs]
            demands += [
                (
                    MaterialRequirement(
                        requirement_id=f"loss-{i}",
                        spec_id=loss.spec_id,
                        quantity_kind="EXACT",
                        quantity=loss.quantity,
                        provenance_refs=loss.provenance_refs,
                    ),
                    True,
                )
                for i, loss in enumerate(operation.losses)
            ]
            for requirement, is_loss in demands:
                supply = supplies.get((instance.recipe_instance_id, requirement.spec_id))
                source = source_supplies.get(requirement.spec_id)
                if supply is None or source is None:
                    scan.fail(
                        "MATERIAL_BINDING", "需求没有对应来源供应", task.root, requirement.spec_id
                    )
                    continue
                try:
                    share = _share(requirement, source[0])
                    if share <= 0:
                        raise ValueError("消耗份额必须为正")
                except (ValueError, ZeroDivisionError) as exc:
                    scan.fail("MATERIAL_QUANTITY", str(exc), task.root, requirement.spec_id)
                    continue
                expected_demands[
                    (task, supply.supply_id, requirement_value(requirement), share, is_loss)
                ] += 1
                if task not in fixed and task not in supplied:
                    consumed[supply.supply_id] += share
                producer_task = source[1]
                if (
                    producer_task is not None
                    and producer_task in scan.intervals
                    and task in scan.intervals
                    and task not in supplied
                ):
                    if scan.intervals[task].start_sec < scan.intervals[producer_task].end_sec:
                        scan.fail(
                            "MATERIAL_AVAILABILITY",
                            "加工产物形成前已消费",
                            task.root,
                            requirement.spec_id,
                        )
    actual_demands = Counter(
        (
            d.task_id,
            d.supply_id,
            requirement_value(d.requirement),
            Fraction(d.share_numerator, d.share_denominator),
            d.is_loss,
        )
        for d in problem.material_flow.demands
    )
    if expected_demands != actual_demands or expected_supply_keys != set(supplies):
        scan.fail("MATERIAL_BINDING", "编译物料供需遗漏、重复或份额篡改")
    task_map = {t.task_id: t for t in problem.logical_tasks}
    check_running_materials(scan, consumed)
    for carrier in (
        *problem.standalone_candidates,
        *problem.shared_prep_candidates,
        *problem.thermal_batch_candidates,
    ):
        if any(t not in scan.operations for t in carrier.covers):
            continue
        for port_name in ("material_inputs", "material_outputs"):
            expected_ports = []
            for tid in carrier.covers:
                task_model = task_map[tid]
                for requirement in getattr(scan.operations[tid], port_name):
                    supply = supplies.get((task_model.recipe_instance_id, requirement.spec_id))
                    if supply is not None:
                        expected_ports.append(
                            requirement_value(
                                requirement, spec_id=supply.supply_id, requirement_id="port"
                            )
                        )
            actual_ports = [
                requirement_value(r, requirement_id="port") for r in getattr(carrier, port_name)
            ]
            if Counter(expected_ports) != Counter(actual_ports):
                scan.fail("MATERIAL_BINDING", "候选物料端口偏离来源", carrier.carrier_id.root)
    for supply in supplies.values():
        lots = [lot for lot in scan.runtime.material_lots if lot.spec_id == supply.supply_id]
        producer = fixed.get(supply.producer_task_id) if supply.producer_task_id else None
        if scan.runtime.details is not None:
            check_actual_supply(scan, supply, producer, consumed[supply.supply_id])
            continue
        available = Fraction(1)
        if lots or (
            producer is not None and producer.status == "COMPLETED" and consumed[supply.supply_id]
        ):
            available = Fraction(0)
            quantity = supply.requirement.quantity
            if not lots or quantity is None:
                scan.fail("MATERIAL_STOCK", "已完成产物缺少可核对的实际库存", supply.supply_id)
                continue
            for lot in lots:
                if (
                    lot.quality_status != "QUALIFIED"
                    or scan.runtime.time_origin.offset(lot.produced_at)
                    > scan.runtime.now_offset_sec
                    or (
                        supply.producer_task_id is not None
                        and (producer is None or producer.status != "COMPLETED")
                    )
                ):
                    scan.fail("MATERIAL_STOCK", "实际产物尚未形成或未经合格确认", lot.lot_id.root)
                if producer is not None and (
                    lot.produced_by_execution_id != producer.execution_id
                    or not any(
                        m.lot_id == lot.lot_id
                        and m.spec_id == lot.spec_id
                        and m.quantity == lot.quantity_produced
                        for m in producer.produced
                    )
                ):
                    scan.fail("MATERIAL_STOCK", "库存数量与实际生产事件不同", lot.lot_id.root)
                try:
                    zero = ScaledQuantity(value=0, unit=quantity.unit, scale=1)
                    values = [
                        zero.add(q)
                        for q in (
                            lot.quantity_produced,
                            lot.quantity_available,
                            lot.quantity_reserved,
                        )
                    ]
                    produced, remaining, reserved = [Fraction(v.value, v.scale) for v in values]
                    if not 0 <= reserved <= remaining <= produced:
                        raise ValueError("可用库存、预留或产出数量不守恒")
                    available += (remaining - reserved) / Fraction(quantity.value, quantity.scale)
                except (ValueError, ZeroDivisionError) as exc:
                    scan.fail("MATERIAL_STOCK", str(exc), lot.lot_id.root)
            if set(supply.actual_lot_ids) != {lot.lot_id for lot in lots}:
                scan.fail("MATERIAL_BINDING", "IR 未绑定实际库存批次", supply.supply_id)
        if consumed[supply.supply_id] > available:
            scan.fail("MATERIAL_OVERCONSUMED", "未来需求超过真实可用供应", supply.supply_id)
        if (
            Fraction(supply.available_share_numerator, supply.available_share_denominator)
            != available
        ):
            scan.fail("MATERIAL_BINDING", "IR 可用份额与独立计算不一致", supply.supply_id)
    if len({lot.lot_id for lot in scan.runtime.material_lots}) != len(scan.runtime.material_lots):
        scan.fail("MATERIAL_STOCK", "实际物料批次重复")
    if not {lot.spec_id for lot in scan.runtime.material_lots} <= {
        s.supply_id for s in supplies.values()
    }:
        scan.fail("MATERIAL_STOCK", "库存规格未绑定本菜单实例")
