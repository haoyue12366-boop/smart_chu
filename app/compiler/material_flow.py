"""编译实例内物料供给与份额；半成品库存必须有实际产出和合格依据。"""

import re
from collections import defaultdict
from fractions import Fraction

from app.compiler.actual_materials import actual_supply
from app.domain.candidates import InstantiationResult, stable_id
from app.domain.ids import TaskId
from app.domain.knowledge import MenuKnowledgeView
from app.domain.material import MaterialRequirement
from app.domain.material_flow import (
    MaterialAllocationModel,
    MaterialDemand,
    MaterialFlow,
    MaterialSupply,
    PlannedMaterialAllocation,
    PlannedMaterialRemainder,
)
from app.domain.processing_rules import ProcessingRule
from app.domain.quantity import ScaledQuantity
from app.domain.recovery import retained_input_tasks
from app.domain.runtime_snapshot import RuntimeSnapshot
from app.domain.scheduling_problem import CandidateCarrier, LogicalTask


def _share(demand: MaterialRequirement, supply: MaterialRequirement) -> Fraction:
    if demand.quantity and supply.quantity:
        converted = ScaledQuantity(value=0, unit=supply.quantity.unit, scale=1).add(demand.quantity)
        return Fraction(converted.value, converted.scale) / Fraction(
            supply.quantity.value, supply.quantity.scale
        )
    if demand.quantity is None and supply.quantity is None:
        match = re.search(
            r"(?:原配方份额|本物料整批的)(\d+(?:/\d+)?)", demand.qualitative_quantity or ""
        )
        if match:
            return Fraction(match[1])
    raise ValueError("物料需求缺少精确数量或明确的整批份额")


def compile_material_flow(
    instantiated: InstantiationResult,
    knowledge: MenuKnowledgeView,
    runtime: RuntimeSnapshot,
    optional_inventory_tasks: frozenset[TaskId] = frozenset(),
) -> MaterialFlow:
    recipes = {r.recipe_id: r for r in knowledge.recipes}
    supplies: list[MaterialSupply] = []
    demands: list[MaterialDemand] = []
    consumed: dict[str, Fraction] = defaultdict(Fraction)
    retained = retained_input_tasks(runtime)
    facts = {
        tid: e
        for e in runtime.executions
        for tid in e.task_ids
        if e.status in {"COMPLETED", "RUNNING"}
    }
    for instance in instantiated.menu:
        recipe = recipes[instance.recipe_id]
        tasks = tuple(
            t for t in instantiated.tasks if t.recipe_instance_id == instance.recipe_instance_id
        )
        by_spec = {
            m.spec_id: MaterialSupply(
                supply_id=stable_id("material", instance.recipe_instance_id.root, m.spec_id),
                recipe_instance_id=instance.recipe_instance_id,
                source_spec_id=m.spec_id,
                requirement=m,
            )
            for m in recipe.ingredient_requirements
        }
        for task in tasks:
            for output in task.operation.material_outputs:
                if output.spec_id in by_spec:
                    raise ValueError("同一实例物料存在多个生产者")
                by_spec[output.spec_id] = MaterialSupply(
                    supply_id=stable_id(
                        "material", instance.recipe_instance_id.root, output.spec_id
                    ),
                    recipe_instance_id=instance.recipe_instance_id,
                    source_spec_id=output.spec_id,
                    requirement=output,
                    producer_task_id=task.task_id,
                )
        for task in tasks:
            losses = tuple(
                MaterialRequirement(
                    requirement_id=f"loss-{i}",
                    spec_id=loss.spec_id,
                    quantity_kind="EXACT",
                    quantity=loss.quantity,
                    provenance_refs=loss.provenance_refs,
                )
                for i, loss in enumerate(task.operation.losses)
            )
            for requirement in (*task.operation.material_inputs, *losses):
                supply = by_spec.get(requirement.spec_id)
                if supply is None:
                    raise ValueError("物料消耗缺少可追溯供给")
                share = _share(requirement, supply.requirement)
                if share <= 0:
                    raise ValueError("消耗份额必须为正")
                demands.append(
                    MaterialDemand(
                        demand_id=stable_id(
                            "demand", task.task_id.root, requirement.requirement_id
                        ),
                        task_id=task.task_id,
                        supply_id=supply.supply_id,
                        requirement=requirement,
                        share_numerator=share.numerator,
                        share_denominator=share.denominator,
                        is_loss=requirement in losses,
                    )
                )
                if (
                    task.task_id not in facts
                    and task.task_id not in instantiated.completed_task_ids
                    and task.task_id not in optional_inventory_tasks
                    and task.task_id not in retained
                ):
                    consumed[supply.supply_id] += share
        for supply in by_spec.values():
            needed = consumed[supply.supply_id]
            actual = facts.get(supply.producer_task_id) if supply.producer_task_id else None
            if runtime.details is not None:
                supplies.append(actual_supply(supply, runtime, actual, needed))
                continue
            lots = tuple(lot for lot in runtime.material_lots if lot.spec_id == supply.supply_id)
            available = Fraction(1)
            if lots and actual and actual.status == "RUNNING":
                raise ValueError("运行中工序产物不能作为已完成库存")
            if lots and supply.producer_task_id is not None and actual is None:
                raise ValueError("计划生产者尚无实际产出，不能使用其未来库存")
            if lots or (actual and actual.status == "COMPLETED" and needed):
                if not lots or supply.requirement.quantity is None:
                    raise ValueError("已完成工序缺少数量可核对的实际合格产物")
                available = Fraction(0)
                total = supply.requirement.quantity
                for lot in lots:
                    if (
                        lot.quality_status != "QUALIFIED"
                        or runtime.time_origin.offset(lot.produced_at) > runtime.now_offset_sec
                    ):
                        raise ValueError("物料尚未形成或合格状态未知")
                    if actual:
                        if lot.produced_by_execution_id != actual.execution_id or not any(
                            m.lot_id == lot.lot_id and m.spec_id == lot.spec_id
                            for m in actual.produced
                        ):
                            raise ValueError("实际库存与生产事件不一致")
                    zero = ScaledQuantity(value=0, unit=total.unit, scale=1)
                    free, reserved = (
                        zero.add(lot.quantity_available),
                        zero.add(lot.quantity_reserved),
                    )
                    produced = zero.add(lot.quantity_produced)
                    if Fraction(free.value, free.scale) > Fraction(produced.value, produced.scale):
                        raise ValueError("库存可用量超过实际生产量")
                    amount = Fraction(free.value, free.scale) - Fraction(
                        reserved.value, reserved.scale
                    )
                    if amount < 0:
                        raise ValueError("库存预留超过可用量")
                    available += amount / Fraction(total.value, total.scale)
                supply = supply.model_copy(
                    update={
                        "actual_lot_ids": tuple(lot.lot_id for lot in lots),
                        "available_at_sec": runtime.now_offset_sec,
                        "available_share_numerator": available.numerator,
                        "available_share_denominator": available.denominator,
                    }
                )
            if needed > available:
                raise ValueError("库存不足或物料被重复消耗")
            supplies.append(supply)
    if not {lot.spec_id for lot in runtime.material_lots} <= {s.supply_id for s in supplies}:
        raise ValueError("实际库存含未知规格或缺少菜谱实例映射")
    if len({lot.lot_id for lot in runtime.material_lots}) != len(runtime.material_lots):
        raise ValueError("实际库存批次身份重复")
    return MaterialFlow(supplies=tuple(supplies), demands=tuple(demands))


def bind_candidate_materials(
    candidates: tuple[CandidateCarrier, ...], instantiated: InstantiationResult
) -> tuple[CandidateCarrier, ...]:
    tasks = {t.task_id: t for t in instantiated.tasks}
    result = []
    for candidate in candidates:
        task = tasks[candidate.covers[0]]

        def bind(requirement: MaterialRequirement, task: LogicalTask = task) -> MaterialRequirement:
            return requirement.model_copy(
                update={
                    "spec_id": stable_id(
                        "material", task.recipe_instance_id.root, requirement.spec_id
                    ),
                    "requirement_id": stable_id(
                        "requirement", task.task_id.root, requirement.requirement_id
                    ),
                }
            )

        result.append(
            candidate.model_copy(
                update={
                    "material_inputs": tuple(bind(m) for m in candidate.material_inputs),
                    "material_outputs": tuple(bind(m) for m in candidate.material_outputs),
                }
            )
        )
    return tuple(result)


def build_material_allocations(
    carriers: tuple[CandidateCarrier, ...],
    demands: MaterialFlow,
    rules: tuple[ProcessingRule, ...],
    inventory_rule_ids: tuple[str, ...] = (),
) -> MaterialAllocationModel:
    """绑定源产物的净产量；损耗仍作为需求扣减，不推算未知出成率。

    输出保留源供应身份。合并原料名称不构成合并物料池的依据。
    规则工艺权限由生成器与独立Validator分别核对，本层核对引用与数量。
    """
    supplies = {s.supply_id: s for s in demands.supplies}
    known_rules = {r.rule_id for r in rules} | set(inventory_rule_ids)
    allocations = []
    remainders = []
    for carrier in carriers:
        if carrier.kind != "STANDALONE" and (
            not carrier.rule_refs or not set(carrier.rule_refs) <= known_rules
        ):
            raise ValueError("共享物料分配缺少规则引用")
        if len({o.spec_id for o in carrier.material_outputs}) != len(carrier.material_outputs):
            raise ValueError("载体产物端口重复")
        for output in carrier.material_outputs:
            supply = supplies.get(output.spec_id)
            if supply is None or supply.producer_task_id not in carrier.covers:
                raise ValueError("计划产出没有被当前载体覆盖的来源生产者")
            if (
                output.model_copy(
                    update={
                        "spec_id": supply.requirement.spec_id,
                        "requirement_id": supply.requirement.requirement_id,
                    }
                )
                != supply.requirement
            ):
                raise ValueError("载体净产量或规格偏离来源")
            total = Fraction(0)
            for demand in demands.demands:
                if demand.supply_id != supply.supply_id:
                    continue
                share = _share(demand.requirement, supply.requirement)
                if share <= 0 or share != Fraction(
                    demand.share_numerator, demand.share_denominator
                ):
                    raise ValueError("需求数量和份额不一致")
                total += share
                allocations.append(
                    PlannedMaterialAllocation(
                        producer_carrier_id=carrier.carrier_id,
                        supply_id=supply.supply_id,
                        demand_id=demand.demand_id,
                        consumer_task_id=demand.task_id,
                        share_numerator=share.numerator,
                        share_denominator=share.denominator,
                        is_loss=demand.is_loss,
                    )
                )
            if total > 1:
                raise ValueError("分配及损耗超过计划净产量")
            remainder = 1 - total
            remainders.append(
                PlannedMaterialRemainder(
                    producer_carrier_id=carrier.carrier_id,
                    supply_id=supply.supply_id,
                    share_numerator=remainder.numerator,
                    share_denominator=remainder.denominator,
                )
            )
    return MaterialAllocationModel(allocations=tuple(allocations), remainders=tuple(remainders))
