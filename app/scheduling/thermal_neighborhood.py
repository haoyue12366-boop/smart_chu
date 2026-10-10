"""FT重排的局部人工搜索：固定候选路径及热预约，不改执行事实。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.domain.reports import SolverIndexMapping

if TYPE_CHECKING:
    from app.domain.schedule import CandidateSchedule
    from app.scheduling.model_builder import ModelBuilder


def add_thermal_neighborhood(builder: ModelBuilder, seed: CandidateSchedule) -> None:
    problem, model = builder.problem, builder.model
    if problem.policy.search_strategy != "FT_KITCHEN" or seed.problem_hash != problem.problem_hash:
        raise ValueError("局部热预约搜索需要当前FT问题的完整候选")
    chosen = {assignment.carrier_id: assignment for assignment in seed.assignments}
    covered = [task for assignment in seed.assignments for task in assignment.task_ids]
    if len(chosen) != len(seed.assignments) or len(set(covered)) != len(covered):
        raise ValueError("局部搜索候选重复覆盖")
    if set(covered) != builder.starts.keys() - builder.fixed.keys():
        raise ValueError("局部搜索候选缺少完整未来任务")
    carriers = {carrier.carrier_id: carrier for carrier in builder.candidates}
    if not chosen.keys() <= carriers.keys():
        raise ValueError("局部搜索候选包含不存在的加工路径")
    thermal = {
        task.task_id
        for task in problem.logical_tasks
        if task.operation.action in {"HEAT", "PREHEAT"}
    }
    # 同一强制预约的装载、介入和取出保持整体，避免固定热段却改写腔体预约。
    for reservation in problem.mandatory_programs.reservations:
        if thermal.intersection(reservation.members):
            thermal.update(reservation.members)
    before = len(model.proto.constraints)
    variables = []
    for carrier in builder.candidates:
        builder.check_budget()
        assignment = chosen.get(carrier.carrier_id)
        selected = builder.selected[carrier.carrier_id]
        model.add(selected == int(assignment is not None))
        variables.append(selected.name)
        if assignment is None:
            continue
        if set(assignment.task_ids) != set(carrier.covers) or (
            assignment.interval.end_sec - assignment.interval.start_sec != carrier.duration_sec
        ):
            raise ValueError("局部搜索候选的覆盖或时长与当前路径不符")
        if thermal.intersection(carrier.covers):
            start, end = (
                builder.carrier_starts[carrier.carrier_id],
                builder.carrier_ends[carrier.carrier_id],
            )
            model.add(start == assignment.interval.start_sec)
            model.add(end == assignment.interval.end_sec)
            variables.extend((start.name, end.name))
    # 基础模型中的人工互斥已经完整建立。只有本轮人工目标忽略强制缺席
    # 的备选段；历史人工段（没有carrier_id）仍参与连续工作统计。
    builder.human_intervals = [
        phase
        for phase in builder.human_intervals
        if phase.carrier_id is None or phase.carrier_id in chosen
    ]
    builder.additional_mappings.append(
        SolverIndexMapping(
            constraint_id="ft-thermal-neighborhood",
            variable_ids=tuple(variables),
            proto_constraint_indices=tuple(range(before, len(model.proto.constraints))),
        )
    )
