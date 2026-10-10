"""把完整合法计划的人工顺序提示给精确模型；不增加硬约束或删候选。"""

from app.domain.schedule import CandidateSchedule
from app.scheduling.model_builder import ModelBuilder


def add_human_chain_hint(builder: ModelBuilder, hint: CandidateSchedule) -> None:
    builder.check_budget()
    if not builder.human_chain_enabled:
        return
    model = builder.model
    assignments = {a.carrier_id: a for a in hint.assignments}
    spans: dict[int, tuple[int, int]] = {}
    variable_values: dict[int, int] = {}
    for i, phase in enumerate(builder.human_intervals):
        builder.check_budget()
        if phase.carrier_id is not None:
            assignment = assignments.get(phase.carrier_id)
            if assignment is None:
                continue
            start = assignment.interval.start_sec + phase.start_offset_sec
            end = assignment.interval.start_sec + (
                phase.end_offset_sec
                if phase.end_offset_sec is not None
                else assignment.interval.end_sec - assignment.interval.start_sec
            )
        elif phase.tasks and phase.tasks[0] in builder.fixed:
            start, end = builder.fixed[phase.tasks[0]]
            # 显式资源事实可能是任务内的一个短段，以常量段为准。
            if (
                len(phase.start.proto.domain) == 2
                and phase.start.proto.domain[0] == phase.start.proto.domain[1]
            ):
                start, end = phase.start.proto.domain[0], phase.end.proto.domain[0]
        elif (
            phase.start.proto.domain[0]
            == phase.start.proto.domain[len(phase.start.proto.domain) - 1]
            and phase.end.proto.domain[0] == phase.end.proto.domain[len(phase.end.proto.domain) - 1]
        ):
            start, end = phase.start.proto.domain[0], phase.end.proto.domain[0]
        else:
            return  # 部分提示不能推导完整人工顺序。
        spans[i] = start, end
        variable_values[phase.start.index] = start
        variable_values[phase.end.index] = end
    if builder.human_chain_members:
        grouped_spans = {}
        for i, members in enumerate(builder.human_chain_members):
            present = [spans[j] for j in members if j in spans]
            if len(present) > 1:
                return  # 完整合法候选不能同时选择严格互斥的组成员。
            phase = builder.human_chain_intervals[i]
            variable_values[phase.presence.index] = int(bool(present))
            if present:
                grouped_spans[i] = present[0]
        spans = grouped_spans
    order = sorted(spans, key=lambda i: (spans[i][0], spans[i][1], i))
    if any(spans[a][1] > spans[b][0] for a, b in zip(order, order[1:], strict=False)):
        return
    beginnings = {}
    block_start = 0
    prior_end = None
    for i in order:
        start, end = spans[i]
        if prior_end is None or start - prior_end >= builder.problem.policy.objective.rest_gap_sec:
            block_start = start
        beginnings[i] = block_start
        prior_end = end
    values = {"max-human-busy": max((spans[i][1] - beginnings[i] for i in order), default=0)}
    for i in range(len(builder.human_chain_intervals or builder.human_intervals)):
        builder.check_budget()
        values[f"busy-start:{i}"] = beginnings.get(i, 0)
        values[f"human-span:{i}"] = spans[i][1] - beginnings[i] if i in spans else 0
    already = set(model.proto.solution_hint.vars)
    for index, variable in enumerate(model.proto.variables):
        if index % 64 == 0:
            builder.check_budget()
        if variable.name.startswith(("human-before:", "human-gap:")):
            kind, first, second = variable.name.split(":")
            left, right = int(first), int(second)
            values[variable.name] = int(
                left in spans
                and right in spans
                and spans[right][0] - spans[left][1]
                >= (0 if kind == "human-before" else builder.problem.policy.objective.rest_gap_sec)
            )
        elif variable.name.startswith(("human-distance:", "human-rest:")):
            kind, first, second = variable.name.split(":")
            left, right = int(first), int(second)
            gap = (
                max(spans[right][0] - spans[left][1], spans[left][0] - spans[right][1])
                if left in spans and right in spans
                else 0
            )
            values[variable.name] = (
                gap
                if kind == "human-distance"
                else int(gap >= builder.problem.policy.objective.rest_gap_sec)
            )
        if index not in already and (index in variable_values or variable.name in values):
            model.add_hint(
                model.get_int_var_from_proto_index(index),
                variable_values[index] if index in variable_values else values[variable.name],
            )
