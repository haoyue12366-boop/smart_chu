"""独立从运行批次与产出报告验证 P4 可用份额。"""

from fractions import Fraction

from app.domain.advance_preparation import active_preparations
from app.domain.inventory import committed_fulfillments
from app.domain.material_flow import MaterialSupply
from app.domain.runtime_snapshot import ExecutionRecord
from app.validation.schedule_context import Scan


def check_actual_supply(
    scan: Scan, supply: MaterialSupply, producer: ExecutionRecord | None, demand: Fraction
) -> None:
    details = scan.runtime.details
    assert details is not None
    fulfilled = next(
        (
            item
            for item in committed_fulfillments(scan.runtime)
            if supply.producer_task_id in item.task_ids
        ),
        None,
    )
    if fulfilled is not None:
        available = Fraction(0)
        identities = set()
        if supply.supply_id == fulfilled.supply.target_supply_id:
            expected = supply.requirement.quantity
            if expected is None:
                scan.fail("INVENTORY_QUANTITY", "冻结库存目标缺少精确数量", supply.supply_id)
                return
            for claim in fulfilled.supply.lot_claims:
                lot = next((item for item in details.lots if item.lot_id == claim.lot_id), None)
                remaining = (
                    Fraction(claim.quantity.value, claim.quantity.scale) - claim.consumed.fraction()
                )
                if lot is None or remaining < 0:
                    scan.fail("INVENTORY_STOCK", "冻结库存批次缺失或累计消费越界", claim.lot_id)
                    continue
                identities.add(lot.lot_id)
                zero = claim.quantity.model_copy(update={"value": 0})
                actual_left, reserved = (
                    zero.add(lot.exact(lot.available)),
                    zero.add(lot.exact(lot.reserved)),
                )
                if remaining > Fraction(
                    actual_left.value, actual_left.scale
                ) or remaining > Fraction(reserved.value, reserved.scale):
                    scan.fail("INVENTORY_STOCK", "冻结库存的实际余量或预约不足", claim.lot_id)
                now = scan.runtime.time_origin.at(scan.runtime.now_offset_sec)
                expiry = fulfilled.supply.expires_at_sec
                if remaining and (expiry is None or scan.runtime.now_offset_sec >= expiry):
                    scan.fail("INVENTORY_TIME", "冻结库存超过审核使用期限", claim.lot_id)
                if remaining and (
                    lot.quality_status != "QUALIFIED"
                    or lot.produced_at > now
                    or (lot.expires_at and lot.expires_at <= now)
                ):
                    scan.fail("INVENTORY_TIME", "冻结库存失去当前使用资格", claim.lot_id)
                converted = expected.model_copy(update={"value": 0}).add(
                    claim.quantity.model_copy(
                        update={"value": remaining.numerator, "scale": remaining.denominator}
                    )
                )
                available += Fraction(converted.value, converted.scale) / Fraction(
                    expected.value, expected.scale
                )
        if demand > available:
            scan.fail(
                "INVENTORY_OVERCONSUMED", "未来需求超过冻结供应的未消费份额", supply.supply_id
            )
        if (
            Fraction(supply.available_share_numerator, supply.available_share_denominator)
            != available
            or {item.root for item in supply.actual_lot_ids} != identities
        ):
            scan.fail("MATERIAL_BINDING", "冻结库存绑定或剩余份额与事实不同", supply.supply_id)
        return
    actual = producer is not None and (
        producer.status == "COMPLETED" or supply.producer_task_id in producer.completed_task_ids
    )
    failed = {
        execution.execution_id
        for execution in scan.runtime.executions
        if execution.status == "FAILED"
    }
    lots = [
        lot
        for lot in details.lots
        if lot.spec_id == supply.supply_id and lot.produced_by_execution_id not in failed
    ]
    available = Fraction(1)
    identities = set()
    preparation = next(
        (
            item
            for item in active_preparations(scan.runtime)
            if supply.producer_task_id in item.task_ids
        ),
        None,
    )
    if supply.producer_task_id is None or actual or preparation is not None:
        available = Fraction(0)
        for lot in lots:
            if preparation is not None and (
                lot.preparation_id != preparation.preparation_id
                or lot.produced_by_execution_id is not None
            ):
                scan.fail("PREPARATION_SOURCE", "提前备料不能伪装实测完成库存", lot.lot_id)
            if preparation is None and lot.preparation_id is not None:
                scan.fail("PREPARATION_SOURCE", "声明供应没有合法提前备料来源", lot.lot_id)
            if lot.quality_status != "QUALIFIED":
                continue
            identities.add(lot.lot_id)
            if scan.runtime.time_origin.offset(lot.produced_at) > scan.runtime.now_offset_sec:
                scan.fail("MATERIAL_STOCK", "物料在当前时刻尚未产出", lot.lot_id)
            if producer:
                report = next(
                    (
                        m
                        for m in producer.produced
                        if m.lot_id.root == lot.lot_id and m.spec_id == lot.spec_id
                    ),
                    None,
                )
                if report is None or lot.produced_by_execution_id != producer.execution_id:
                    scan.fail("MATERIAL_STOCK", "实际生产报告缺失", lot.lot_id)
                elif report.quantity is not None:
                    if (
                        lot.unit is None
                        or report.quantity.as_decimal()
                        != report.quantity.model_copy(update={"value": 0})
                        .add(lot.exact(lot.produced))
                        .as_decimal()
                    ):
                        scan.fail("MATERIAL_STOCK", "实际产出数量不一致", lot.lot_id)
                elif report.batch_share != lot.produced:
                    scan.fail("MATERIAL_STOCK", "实际定性份额不一致", lot.lot_id)
            free = lot.available.fraction() - lot.reserved.fraction()
            if free < 0:
                scan.fail("MATERIAL_STOCK", "预约超过实有库存", lot.lot_id)
                continue
            quantity = supply.requirement.exact_quantity
            try:
                if quantity is None:
                    if lot.quantity_kind not in {"QUALITATIVE", "RECIPE_BATCH"}:
                        raise ValueError("定性量不得虚构数值单位")
                    available += free
                else:
                    zero = quantity.model_copy(update={"value": 0})
                    left, right = (
                        zero.add(lot.exact(lot.available)),
                        zero.add(lot.exact(lot.reserved)),
                    )
                    available += (
                        Fraction(left.value, left.scale) - Fraction(right.value, right.scale)
                    ) / Fraction(quantity.value, quantity.scale)
            except ValueError as exc:
                scan.fail("MATERIAL_STOCK", str(exc), lot.lot_id)
    elif lots:
        scan.fail("MATERIAL_STOCK", "未完成工序不能产生可用库存", supply.supply_id)
    if demand > available:
        scan.fail("MATERIAL_OVERCONSUMED", "未来需求超过真实可用库存", supply.supply_id)
    if (
        Fraction(supply.available_share_numerator, supply.available_share_denominator) != available
        or {x.root for x in (supply.declared_lot_ids if preparation else supply.actual_lot_ids)}
        != identities
        or (
            preparation is not None
            and (supply.actual_lot_ids or supply.preparation_id != preparation.preparation_id)
        )
        or (preparation is None and (supply.declared_lot_ids or supply.preparation_id is not None))
    ):
        scan.fail("MATERIAL_BINDING", "编译份额或库存身份与事实不符", supply.supply_id)
