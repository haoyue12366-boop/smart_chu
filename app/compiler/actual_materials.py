"""P4 的实际库存份额；定性量仅保留原配方整批份额。"""

from fractions import Fraction

from app.domain.advance_preparation import active_preparations
from app.domain.ids import MaterialLotId
from app.domain.inventory import committed_fulfillments
from app.domain.material_flow import MaterialSupply
from app.domain.runtime_snapshot import ExecutionRecord, RuntimeSnapshot


def actual_supply(
    supply: MaterialSupply,
    runtime: RuntimeSnapshot,
    producer: ExecutionRecord | None,
    needed: Fraction,
) -> MaterialSupply:
    assert runtime.details is not None
    fulfillment = next(
        (
            item
            for item in committed_fulfillments(runtime)
            if supply.producer_task_id in item.task_ids
        ),
        None,
    )
    if fulfillment is not None:
        available = Fraction(0)
        used = []
        if supply.supply_id == fulfillment.supply.target_supply_id:
            quantity = supply.requirement.quantity
            assert quantity is not None
            lots_by_id = {item.lot_id: item for item in runtime.details.lots}
            for claim in fulfillment.supply.lot_claims:
                lot = lots_by_id[claim.lot_id]
                remaining = (
                    Fraction(claim.quantity.value, claim.quantity.scale) - claim.consumed.fraction()
                )
                if remaining < 0:
                    raise ValueError("库存满足记录消费超过已承诺数量")
                zero = claim.quantity.model_copy(update={"value": 0})
                actual_left = zero.add(lot.exact(lot.available))
                reserved = zero.add(lot.exact(lot.reserved))
                if remaining > min(
                    Fraction(actual_left.value, actual_left.scale),
                    Fraction(reserved.value, reserved.scale),
                ):
                    raise ValueError("已冻结的库存供应缺少真实余量或保护预约")
                now = runtime.time_origin.at(runtime.now_offset_sec)
                expiry = fulfillment.supply.expires_at_sec
                if remaining and (expiry is None or runtime.now_offset_sec >= expiry):
                    raise ValueError("已冻结库存超过审核使用期限")
                if remaining and (
                    lot.quality_status != "QUALIFIED"
                    or lot.produced_at > now
                    or (lot.expires_at and lot.expires_at <= now)
                ):
                    raise ValueError("已冻结的库存供应已失去使用资格")
                converted = quantity.model_copy(update={"value": 0}).add(
                    claim.quantity.model_copy(
                        update={"value": remaining.numerator, "scale": remaining.denominator}
                    )
                )
                available += Fraction(converted.value, converted.scale) / Fraction(
                    quantity.value, quantity.scale
                )
                used.append(MaterialLotId(lot.lot_id))
        if needed > available:
            raise ValueError("库存满足记录的未消费份额不足")
        return supply.model_copy(
            update={
                "actual_lot_ids": tuple(used),
                "available_at_sec": runtime.now_offset_sec,
                "available_share_numerator": available.numerator,
                "available_share_denominator": available.denominator,
            }
        )
    failed_attempts = {
        record.execution_id for record in runtime.executions if record.status == "FAILED"
    }
    # 失败批次保留在历史账中；重做的供给只来自新的执行身份。
    lots = [
        lot
        for lot in runtime.details.lots
        if lot.spec_id == supply.supply_id and lot.produced_by_execution_id not in failed_attempts
    ]
    completed = producer is not None and (
        producer.status == "COMPLETED" or supply.producer_task_id in producer.completed_task_ids
    )
    preparation = next(
        (item for item in active_preparations(runtime) if supply.producer_task_id in item.task_ids),
        None,
    )
    if supply.producer_task_id is not None and not completed and preparation is None:
        if lots:
            raise ValueError("产物没有对应已完成的实际工序")
        return supply
    if not lots and needed:
        raise ValueError("实际原料或已完成产物缺少合格库存")
    available = Fraction(0)
    used = []
    for lot in lots:
        if preparation is not None and (
            lot.preparation_id != preparation.preparation_id
            or lot.produced_by_execution_id is not None
        ):
            raise ValueError("提前备料供应必须保持用户策略声明来源")
        if preparation is None and lot.preparation_id is not None:
            raise ValueError("未授权的提前备料不能作为实际产物")
        if lot.quality_status != "QUALIFIED":
            continue
        if runtime.time_origin.offset(lot.produced_at) > runtime.now_offset_sec:
            raise ValueError("实际产物尚未形成")
        if producer and (
            lot.produced_by_execution_id != producer.execution_id
            or not any(
                m.lot_id.root == lot.lot_id and m.spec_id == lot.spec_id for m in producer.produced
            )
        ):
            raise ValueError("产物来源与执行记录不一致")
        free = lot.available.fraction() - lot.reserved.fraction()
        if free < 0:
            raise ValueError("预约超过实际余量")
        if supply.requirement.exact_quantity is None:
            if lot.quantity_kind not in {"QUALITATIVE", "RECIPE_BATCH"}:
                raise ValueError("定性份额不可擅自换算")
            available += free
        else:
            quantity = supply.requirement.exact_quantity
            assert quantity is not None
            zero = quantity.model_copy(update={"value": 0})
            converted = zero.add(lot.exact(lot.available))
            reserved = zero.add(lot.exact(lot.reserved))
            available += (
                Fraction(converted.value, converted.scale)
                - Fraction(reserved.value, reserved.scale)
            ) / Fraction(quantity.value, quantity.scale)
        used.append(MaterialLotId(lot.lot_id))
    if needed > available:
        raise ValueError("实际库存不足，不能恢复已消费的材料")
    return supply.model_copy(
        update={
            "actual_lot_ids": tuple(used) if preparation is None else (),
            "declared_lot_ids": tuple(used) if preparation is not None else (),
            "preparation_id": preparation.preparation_id if preparation is not None else None,
            "available_at_sec": runtime.now_offset_sec,
            "available_share_numerator": available.numerator,
            "available_share_denominator": available.denominator,
        }
    )
