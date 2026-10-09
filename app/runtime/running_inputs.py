"""运行中未投入的数量仍需实物支持，取消预约不能取消工艺投入。"""

from collections import defaultdict
from fractions import Fraction

from app.domain.quantity import Unit
from app.domain.runtime_session import RuntimeSession
from app.runtime.material_ledger import movement_amount
from app.runtime.material_reservations import amount_in


def check_running_inputs(session: RuntimeSession) -> None:
    details = session.runtime.details
    assert details is not None
    lots = {lot.lot_id: lot for lot in details.lots}
    now = session.runtime.time_origin.at(session.runtime.now_offset_sec)
    capacities = {
        lot.lot_id: lot.available.fraction() - lot.reserved.fraction()
        for lot in details.lots
        if lot.quality_status == "QUALIFIED"
        and lot.produced_at <= now
        and (lot.expires_at is None or lot.expires_at > now)
    }
    obligations: list[tuple[Fraction, Unit | None, tuple[str, ...]]] = []
    protected: set[str] = set()
    for execution in session.runtime.executions:
        if execution.status != "RUNNING" or execution.recovery_kind == "RESUME":
            continue
        binding = next(
            (
                item
                for item in session.bindings
                if item.carrier.carrier_id.root == execution.carrier_id
            ),
            None,
        )
        if binding is None:
            raise ValueError("运行中执行缺少原始投入绑定")
        started = set(execution.started_task_ids)
        expected: dict[tuple[str, Unit | None, tuple[str, ...]], Fraction] = defaultdict(Fraction)
        for reservation in session.future_allocations:
            if (
                reservation.task_id not in started
                or reservation.plan_version != binding.plan_version
            ):
                continue
            eligible = reservation.eligible_lot_ids or tuple(
                lot.lot_id for lot in details.lots if lot.spec_id == reservation.supply_id
            )
            key = (reservation.supply_id, reservation.unit, tuple(sorted(eligible)))
            expected[key] += reservation.amount.fraction()
            protected.add(reservation.reservation_id)
        for (_, unit, eligible), amount in expected.items():
            actual = sum(
                (
                    amount_in(
                        movement_amount(movement, lots[movement.lot_id.root]),
                        lots[movement.lot_id.root].unit,
                        unit,
                    )
                    for movement in execution.consumed
                    if movement.lot_id.root in eligible
                ),
                Fraction(0),
            )
            if amount > actual:
                obligations.append((amount - actual, unit, eligible))
    # 自己仍未消费的预约可以支持原执行，其他执行的预约仍不可挪用。
    for allocation in session.allocations:
        if (
            allocation.status == "RESERVED"
            and allocation.reservation_id in protected
            and allocation.lot_id in capacities
        ):
            capacities[allocation.lot_id] += (
                allocation.amount.fraction() - allocation.consumed.fraction()
            )
    for needed, unit, eligible in sorted(obligations, key=lambda item: len(item[2])):
        for lot_id in eligible:
            if needed <= 0:
                break
            if lot_id not in lots:
                raise ValueError("运行中投入引用的实物批次缺失")
            free = max(Fraction(0), capacities.get(lot_id, Fraction(0)))
            take = min(free, amount_in(needed, unit, lots[lot_id].unit))
            capacities[lot_id] = free - take
            needed -= amount_in(take, lots[lot_id].unit, unit)
        if needed > 0:
            raise ValueError("运行中工序剩余投入不足，须补充实物或确认失败")
