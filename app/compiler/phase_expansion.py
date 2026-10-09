"""将已明确拆分的真实程序映射到任务端口，不猜测缺失阶段。"""

from app.compiler.instantiate import failure
from app.compiler.mandatory_batches import fixed_batches
from app.domain.candidates import InstantiationResult, stable_id
from app.domain.knowledge import MenuKnowledgeView
from app.domain.recipe_context import RecipeProgram
from app.domain.reports import CompilationFailure
from app.domain.scheduling_problem import LogicalTask
from app.domain.thermal import ExpandedPrograms, TaskProgram, TaskReservation, TaskTimeRelation


def _program(
    source: RecipeProgram, tasks: tuple[LogicalTask, ...]
) -> tuple[TaskProgram, tuple[TaskTimeRelation, ...]]:
    by_id = {t.operation_id: t for t in tasks}
    before = tuple(by_id[o] for o in source.before_group)
    intervention = tuple(by_id[o] for o in source.intervention_group)
    after = tuple(by_id[o] for o in source.after_group)
    heat_before = tuple(t for t in before if t.operation.action == "HEAT")
    preparation = tuple(t for t in before if t.operation.action != "HEAT")
    if not heat_before or not intervention or not after:
        raise ValueError("固定程序缺少有效加热或介入阶段")
    if any(t.operation.action != "HEAT" for t in after):
        raise ValueError("固定程序后段含未经说明的非热处理阶段")
    if any(
        not any(r.resource_type == "HUMAN" for r in t.operation.resource_requirements)
        for t in intervention
    ):
        raise ValueError("必要介入缺少人工区间")

    def duration(group: tuple[LogicalTask, ...]) -> int:
        values = [t.operation.duration.execution_sec for t in group]
        if any(v is None for v in values):
            raise ValueError("固定程序缺少确定时长")
        return sum(v for v in values if v is not None)

    if (
        duration(heat_before) != source.before_intervention_sec
        or duration(after) != source.remaining_sec
        or duration(intervention) != source.intervention_sec
        or source.active_process_sec != source.before_intervention_sec + source.remaining_sec
    ):
        raise ValueError("固定程序有效加热或介入时长不一致")
    trigger = (
        source.remaining_sec
        if source.trigger_type == "remaining_time"
        else source.before_intervention_sec
    )
    if source.trigger_value != trigger or not source.timer_paused:
        raise ValueError("固定程序触发值矛盾或缺少计时不停顿时的热暴露映射")
    refs = tuple(
        dict.fromkeys(
            ref for t in (*before, *intervention, *after) for ref in t.operation.provenance_refs
        )
    )
    ordered = (*heat_before, *intervention, *after)
    relations = tuple(
        TaskTimeRelation(
            left_task=a.task_id,
            left_anchor="END",
            right_task=b.task_id,
            right_anchor="START",
            min_offset_sec=0,
            max_offset_sec=0,
            evidence_refs=refs,
        )
        for a, b in zip(ordered, ordered[1:], strict=False)
    )
    return TaskProgram(
        program_id=stable_id("program", tasks[0].recipe_instance_id.root, source.program_id),
        recipe_instance_id=tasks[0].recipe_instance_id,
        preparation_members=tuple(t.task_id for t in preparation),
        before_members=tuple(t.task_id for t in heat_before),
        intervention_members=tuple(t.task_id for t in intervention),
        after_members=tuple(t.task_id for t in after),
        active_process_sec=source.active_process_sec,
        before_intervention_sec=source.before_intervention_sec,
        intervention_sec=source.intervention_sec,
        remaining_sec=source.remaining_sec,
        timer_paused=source.timer_paused,
        evidence_refs=refs,
    ), relations


def expand_required_programs(
    instantiated: InstantiationResult, knowledge: MenuKnowledgeView
) -> ExpandedPrograms | CompilationFailure:
    contexts = {c.recipe_id: c for c in knowledge.recipe_contexts}
    devices = {d.device_instance_id for d in knowledge.devices}
    reservations: list[TaskReservation] = []
    programs: list[TaskProgram] = []
    relations: list[TaskTimeRelation] = []
    try:
        for instance in instantiated.menu:
            tasks = tuple(
                t for t in instantiated.tasks if t.recipe_instance_id == instance.recipe_instance_id
            )
            by_id = {t.operation_id: t for t in tasks}
            context = contexts.get(instance.recipe_id)
            if context:
                for reservation in context.resource_reservations:
                    if (
                        not reservation.members
                        or not reservation.resource_options
                        or not set(reservation.resource_options) <= devices
                    ):
                        raise ValueError("菜内预约缺少成员或真实设备")
                    members = tuple(by_id[o] for o in reservation.members)
                    fixed_layers = {
                        use.occupied_layer_indices
                        for member in members
                        for use in member.operation.resource_requirements
                        if use.resource_id in reservation.resource_options
                    }
                    if len(fixed_layers) > 1 and any(fixed_layers):
                        raise ValueError("同一连续设备预约的固定层位集合不一致")
                    reservations.append(
                        TaskReservation(
                            reservation_id=stable_id(
                                "reservation",
                                instance.recipe_instance_id.root,
                                reservation.reservation_id,
                            ),
                            recipe_instance_id=instance.recipe_instance_id,
                            members=tuple(t.task_id for t in members),
                            resource_options=reservation.resource_options,
                            conflict_policy=reservation.policy,
                            evidence_refs=tuple(
                                dict.fromkeys(
                                    ref for t in members for ref in t.operation.provenance_refs
                                )
                            ),
                        )
                    )
                for program in context.program_constraints:
                    bound, timing = _program(program, tasks)
                    programs.append(bound)
                    relations.extend(timing)
            for task in tasks:
                for intervention in task.operation.execution_policy.interventions:
                    target = by_id[intervention.operation_id]
                    if not any(
                        r.resource_type == "HUMAN" for r in target.operation.resource_requirements
                    ):
                        raise ValueError("介入端口缺少人工阶段")
                    relations.append(
                        TaskTimeRelation(
                            left_task=task.task_id,
                            left_anchor="START",
                            right_task=target.task_id,
                            right_anchor="START",
                            min_offset_sec=intervention.offset_min_sec,
                            max_offset_sec=intervention.offset_max_sec,
                            evidence_refs=task.operation.provenance_refs,
                        )
                    )
        return ExpandedPrograms(
            reservations=tuple(reservations),
            fixed_batches=fixed_batches(instantiated),
            programs=tuple(programs),
            time_relations=tuple(relations),
        )
    except (KeyError, ValueError) as exc:
        return failure("固定程序无法完整展开：" + str(exc))
