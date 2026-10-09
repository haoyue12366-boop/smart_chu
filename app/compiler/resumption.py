"""从已落账的恢复授权生成剩余载体，不修改发布的菜谱工艺。"""

from app.domain.base import content_hash
from app.domain.candidates import stable_id
from app.domain.ids import CarrierId
from app.domain.runtime_snapshot import RuntimeSnapshot
from app.domain.scheduling_problem import CandidateCarrier, LogicalTask


def resume_candidates(
    candidates: tuple[CandidateCarrier, ...], runtime: RuntimeSnapshot
) -> tuple[CandidateCarrier, ...]:
    pending = [
        e for e in runtime.executions if e.status == "PENDING" and e.recovery_kind == "RESUME"
    ]
    result = []
    for carrier in candidates:
        retries = [e for e in pending if set(e.task_ids).intersection(carrier.covers)]
        if not retries:
            result.append(carrier)
            continue
        if len(retries) != 1:
            raise ValueError("续做候选跨越多个独立恢复执行")
        retry = retries[0]
        proof = retry.resumption
        if proof is None:
            raise ValueError("续做事实缺少完整审核工艺和实际保留投入")
        if (
            set(carrier.covers) != set(retry.task_ids)
            or carrier.kind != proof.procedure.source_carrier_kind
            or carrier.resource_uses != proof.procedure.resource_uses
            or carrier.duration_sec != proof.original_duration_sec
            or carrier.member_offsets
            or carrier.resource_phases
        ):
            continue
        result.append(
            carrier.model_copy(
                update={
                    "carrier_id": CarrierId(
                        stable_id(
                            "resume",
                            carrier.carrier_id.root,
                            retry.execution_id.root,
                            content_hash(proof),
                        )
                    ),
                    "duration_sec": proof.authorized_duration_sec,
                    "resume_execution_id": retry.execution_id,
                }
            )
        )
    return tuple(result)


def resume_bounds(
    tasks: tuple[LogicalTask, ...], runtime: RuntimeSnapshot
) -> tuple[LogicalTask, ...]:
    deadlines = {
        task: runtime.time_origin.offset(record.resumption.latest_start_at)
        + record.resumption.authorized_duration_sec
        for record in runtime.executions
        if record.status == "PENDING" and record.resumption is not None
        for task in record.task_ids
    }
    return tuple(
        task.model_copy(
            update={
                "latest_end_sec": min(
                    deadlines[task.task_id], task.latest_end_sec or deadlines[task.task_id]
                )
            }
        )
        if task.task_id in deadlines
        else task
        for task in tasks
    )
