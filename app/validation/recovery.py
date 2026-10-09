"""独立核对失败后新执行的授权链和完整成员边界。"""

from app.domain.ids import ExecutionId
from app.domain.scheduling_problem import CandidateCarrier
from app.validation.schedule_context import Scan


def replaced_failures(scan: Scan) -> set[ExecutionId]:
    replaced = set()
    records = {item.execution_id: item for item in scan.runtime.executions}
    for child in records.values():
        parent = records.get(child.previous_execution_id) if child.previous_execution_id else None
        if parent is None or parent.status != "FAILED":
            continue
        if not child.recovery_rule_id or not child.recovery_kind or not child.event_refs:
            continue
        if set(child.task_ids) != set(parent.task_ids) or len(child.task_ids) != len(
            set(child.task_ids)
        ):
            scan.fail("RECOVERY_SCOPE", "重试身份改变了原失败的完整成员", child.execution_id.root)
            continue
        replaced.add(parent.execution_id)
    return replaced


def check_retry_assignment(scan: Scan, carrier: CandidateCarrier) -> None:
    for retry in scan.runtime.executions:
        if retry.status != "PENDING" or retry.previous_execution_id is None:
            continue
        if not set(carrier.covers).intersection(retry.task_ids):
            continue
        if (
            not retry.recovery_rule_id
            or not retry.recovery_kind
            or set(carrier.covers) != set(retry.task_ids)
            or carrier.kind == "INVENTORY_SUPPLY"
        ):
            scan.fail(
                "RECOVERY_SCOPE", "候选拆分、扩充或跳过了已批准重试的成员", carrier.carrier_id.root
            )
