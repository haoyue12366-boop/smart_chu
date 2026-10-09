"""续做授权保留实际投入；不把已消费的物料还原成可用原料。"""

from collections import defaultdict
from datetime import timedelta
from fractions import Fraction

from app.domain.base import content_hash
from app.domain.events import ExecutionPayload, RuntimeEvent
from app.domain.quantity import Unit
from app.domain.recovery import ResumptionEvidence
from app.domain.runtime_session import RecoveryRule, RuntimeSession
from app.domain.runtime_snapshot import ExecutionRecord
from app.runtime.material_ledger import movement_amount
from app.runtime.material_reservations import amount_in


def authorize_resumption(
    session: RuntimeSession, event: RuntimeEvent, failed: ExecutionRecord, rule: RecoveryRule
) -> ResumptionEvidence:
    payload = event.payload
    assert isinstance(payload, ExecutionPayload)
    procedure = rule.resume_procedure
    binding = next(
        (b for b in session.bindings if b.carrier.carrier_id.root == failed.carrier_id), None
    )
    if procedure is None or not rule.remaining_sec or binding is None:
        raise ValueError("续做缺少完整审核工艺或原执行绑定")
    if (
        binding.carrier.kind != procedure.source_carrier_kind
        or binding.carrier.resource_uses != procedure.resource_uses
        or binding.carrier.resource_phases
        or binding.carrier.member_offsets
        or failed.completed_task_ids
        or len({span.interval for span in failed.task_spans}) != 1
    ):
        raise ValueError("续做工艺未覆盖原载体及完整资源阶段")
    if (
        payload.output_status != "QUALIFIED"
        or failed.failure_output_status in {None, "WASTE"}
        or failed.produced
        or payload.produced
        or any(
            e.execution_id == failed.execution_id.root and e.kind == "LOSS" for e in session.ledger
        )
    ):
        raise ValueError("保留全部投入的续做不能包含未知、报废、损耗或已分离产物")
    assert failed.finished_at is not None
    latest = failed.finished_at + timedelta(seconds=procedure.max_pause_sec)
    if event.occurred_at > latest:
        raise ValueError("已超过审核工艺允许的中断时长")
    retained = {m.lot_id: m for m in payload.consumed}
    original = {m.lot_id: m for m in failed.consumed}
    if not original or set(retained) != set(original) or len(retained) != len(payload.consumed):
        raise ValueError("续做必须逐批确认完整的实际保留投入")
    expected: dict[str, Fraction] = defaultdict(Fraction)
    units: dict[str, Unit | None] = {}
    for allocation in session.future_allocations:
        if (
            allocation.plan_version == binding.plan_version
            and allocation.task_id in failed.task_ids
        ):
            expected[allocation.supply_id] += allocation.amount.fraction()
            units[allocation.supply_id] = allocation.unit
    assert session.runtime.details is not None
    lots = {lot.lot_id: lot for lot in session.runtime.details.lots}
    actual: dict[str, Fraction] = defaultdict(Fraction)
    for lot_id, movement in retained.items():
        lot = lots.get(lot_id.root)
        parent = original[lot_id]
        if lot is None or movement.spec_id != parent.spec_id or movement.spec_id != lot.spec_id:
            raise ValueError("保留投入的批次或规格不符")
        if movement_amount(movement, lot) != movement_amount(parent, lot):
            raise ValueError("续做保留数量不等于原执行实际消费")
        if movement.spec_id not in units:
            raise ValueError("保留投入缺少原计划的完整需求证据")
        actual[movement.spec_id] += amount_in(
            movement_amount(movement, lot), lot.unit, units[movement.spec_id]
        )
    if dict(actual) != dict(expected):
        raise ValueError("原执行尚未投入全部工艺物料，不能按完整保留工艺续做")
    return ResumptionEvidence(
        procedure=procedure,
        retained_inputs=failed.consumed,
        authorized_duration_sec=rule.remaining_sec,
        original_duration_sec=binding.carrier.duration_sec,
        authorization_event_id=event.event_id.root,
        parent_hash=content_hash(failed),
        authorized_at=event.occurred_at,
        latest_start_at=latest,
        source_kind=rule.source_kind,
        evidence_refs=rule.evidence_refs,
    )
