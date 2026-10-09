"""稳定的领域约束目录；求解器变量及 proto 索引另存构建报告。"""

from typing import Literal

from app.domain.base import content_hash
from app.domain.candidates import stable_id
from app.domain.ids import CarrierId, TaskId
from app.domain.scheduling_problem import ConstraintRecord, SchedulingProblem


def build_catalog(problem: SchedulingProblem) -> tuple[ConstraintRecord, ...]:
    records: list[ConstraintRecord] = []

    def add(
        category: str,
        identity: str,
        summary: str,
        *,
        tasks: tuple[TaskId, ...] = (),
        carriers: tuple[CarrierId, ...] = (),
        resources: tuple[str, ...] = (),
        evidence: tuple[str, ...] = (),
        origin: Literal["RECIPE", "DEVICE", "RUNTIME", "COMPILER"] = "RECIPE",
        hardness: Literal["MANDATORY", "POLICY_BOUND", "OPTIMIZATION_BOUND"] = "MANDATORY",
    ) -> None:
        record = ConstraintRecord(
            constraint_id="pending",
            category=category,
            task_ids=tasks,
            carrier_ids=carriers,
            resource_ids=resources,
            evidence_refs=evidence,
            origin=origin,
            hardness=hardness,
            expression_summary=summary,
        )
        identifier = stable_id(
            "constraint",
            content_hash(record),
            identity,
            problem.knowledge_version,
            problem.rule_version,
            problem.policy.policy_version,
        )
        records.append(record.model_copy(update={"constraint_id": identifier}))

    candidates = (
        *problem.standalone_candidates,
        *problem.shared_prep_candidates,
        *problem.thermal_batch_candidates,
        *problem.inventory_supply_candidates,
    )
    covering_by_task: dict[TaskId, list[CarrierId]] = {}
    for candidate in candidates:
        for task_id in candidate.covers:
            covering_by_task.setdefault(task_id, []).append(candidate.carrier_id)
    for task in problem.logical_tasks:
        covering = tuple(covering_by_task.get(task.task_id, ()))
        add(
            "COVERAGE",
            task.task_id.root,
            "必需任务由候选或冻结事实恰好覆盖一次",
            tasks=(task.task_id,),
            carriers=covering,
            evidence=task.operation.provenance_refs,
        )
        add(
            "TIME_DOMAIN",
            task.task_id.root,
            f"开始>={task.earliest_start_sec}；完成<={task.latest_end_sec}；H={problem.horizon_sec}",
            tasks=(task.task_id,),
            origin="COMPILER",
        )
    add(
        "TIME_GRID",
        "grid",
        f"未来时间为{problem.policy.time_grid_sec}秒倍数；输出{problem.policy.minute_output_mode}",
        origin="COMPILER",
        hardness="POLICY_BOUND",
    )
    for c in candidates:
        add(
            "DURATION",
            c.carrier_id.root,
            f"选中时完成-开始={c.duration_sec}秒",
            tasks=c.covers,
            carriers=(c.carrier_id,),
            evidence=c.provenance_refs,
        )
        for i, use in enumerate(c.resource_uses):
            add(
                "RESOURCE",
                f"{c.carrier_id.root}/{i}",
                f"物理占用{use.physical_resource_id}/{use.component_id}，策略{use.conflict_policy}",
                tasks=c.covers,
                carriers=(c.carrier_id,),
                resources=(use.resource_id,),
                evidence=use.evidence_refs,
                origin="DEVICE",
            )
    for i, dep in enumerate(problem.dependencies):
        add(
            "PRECEDENCE",
            str(i),
            f"后继开始-前驱完成在[{dep.min_lag_sec},{dep.max_lag_sec}]秒内",
            tasks=(dep.predecessor_id, dep.successor_id),
            evidence=dep.evidence_refs,
        )
    for supply in problem.material_flow.supplies:
        demands = tuple(
            d.task_id for d in problem.material_flow.demands if d.supply_id == supply.supply_id
        )
        add(
            "MATERIAL",
            supply.supply_id,
            "实例供需守恒；合格实际库存及生产完成后方可消费",
            tasks=demands,
            evidence=supply.requirement.provenance_refs,
        )
    for reservation in problem.mandatory_programs.reservations:
        add(
            "RESERVATION",
            reservation.reservation_id,
            "同一物理资源上连续持有外层预约直至释放",
            tasks=reservation.members,
            resources=reservation.resource_options,
            evidence=reservation.evidence_refs,
        )
    for batch in problem.mandatory_programs.fixed_batches:
        add(
            "MANDATORY_BATCH",
            batch.batch_id,
            "保留菜内强制批次的全部成员",
            tasks=batch.members,
            evidence=batch.evidence_refs,
        )
    for program in problem.mandatory_programs.programs:
        add(
            "FIXED_PROGRAM",
            program.program_id,
            f"加工{program.active_process_sec}秒；介入{program.intervention_sec}秒，计时暂停={program.timer_paused}",
            tasks=(*program.before_members, *program.intervention_members, *program.after_members),
            evidence=program.evidence_refs,
        )
    for i, relation in enumerate(problem.mandatory_programs.time_relations):
        add(
            "TIME_RELATION",
            str(i),
            f"{relation.right_anchor}-{relation.left_anchor}在[{relation.min_offset_sec},{relation.max_offset_sec}]秒内",
            tasks=(relation.left_task, relation.right_task),
            evidence=relation.evidence_refs,
        )
    for execution in problem.fixed_executions:
        add(
            "HISTORY",
            execution.execution_id.root,
            "保留实际开始、完成、占用和有来源的剩余时长",
            tasks=execution.task_ids,
            origin="RUNTIME",
        )
    for i, block in enumerate(problem.resource_blocks):
        add(
            "RESOURCE_BLOCK",
            str(i),
            f"{block.interval}不可用：{block.reason}",
            resources=(block.resource_id,),
            evidence=block.evidence_refs,
            origin="RUNTIME",
        )
    return tuple(records)
