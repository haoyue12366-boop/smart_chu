"""从具体 Greedy 布局投影物理占用及连续外层预约。"""

from app.domain.carrier_timing import task_intervals
from app.domain.execution_timing import execution_intervals
from app.domain.ids import TaskId
from app.domain.resources import ResourceUse
from app.domain.schedule import ScheduledAssignment
from app.domain.scheduling_problem import SchedulingProblem
from app.domain.time import Interval
from app.scheduling.calendar_types import CalendarEntry, TaskPort


def fixed_ports(problem: SchedulingProblem) -> tuple[TaskPort, ...]:
    relevant = {task.task_id for task in problem.logical_tasks}
    result: list[TaskPort] = [
        TaskPort(
            task_id=task,
            interval=Interval(start_sec=item.satisfied_at_sec, end_sec=item.satisfied_at_sec),
        )
        for item in problem.fixed_task_fulfillments
        for task in item.task_ids
    ]
    for fact in problem.fixed_executions:
        if fact.started_at is None:
            raise ValueError("冻结事实缺少开始时刻")
        if fact.finished_at is None and (
            fact.remaining_sec is None or not fact.remaining_source_ref
        ):
            raise ValueError("运行中事实缺少剩余时长")
        result.extend(
            TaskPort(task_id=t, interval=span)
            for t, span in execution_intervals(fact, problem.runtime).items()
            if t in relevant
        )
    return tuple(result)


def entries_for(
    problem: SchedulingProblem,
    assignments: tuple[ScheduledAssignment, ...],
    *,
    include_fixed: bool = False,
) -> tuple[CalendarEntry, ...]:
    devices = {d.device_instance_id: d for d in problem.resources}
    reservation_members = {
        r.reservation_id: frozenset(t.root for t in r.members)
        for r in problem.mandatory_programs.reservations
    }
    reservation_keys = {
        r.reservation_id: {devices[d].competition_key for d in r.resource_options if d in devices}
        for r in problem.mandatory_programs.reservations
    }
    reservation_by_task: dict[tuple[str, tuple[str, str]], str] = {}
    for reservation in problem.mandatory_programs.reservations:
        for task in reservation.members:
            for key in reservation_keys[reservation.reservation_id]:
                reservation_by_task.setdefault((task.root, key), reservation.reservation_id)
    ports = {p.task_id: p.interval for p in fixed_ports(problem)}
    uses: list[tuple[TaskId, ResourceUse, bool]] = []
    shared_members = {}
    thermal = {c.carrier_id: c for c in problem.thermal_batch_candidates}
    joint_entries = []
    entries: list[CalendarEntry] = []
    ports.update(task_intervals(problem, assignments))
    for assignment in assignments:
        shared_members[assignment.task_ids[0]] = assignment.task_ids
        batch = thermal.get(assignment.carrier_id)
        if batch is not None:
            spans = [
                (u, assignment.interval, assignment.task_ids) for u in assignment.resource_uses
            ]
            spans.extend(
                (
                    p.resource_use,
                    Interval(
                        start_sec=assignment.interval.start_sec + p.start_offset_sec,
                        end_sec=assignment.interval.start_sec + p.end_offset_sec,
                    ),
                    (p.task_id,),
                )
                for p in batch.resource_phases
            )
            for use, interval, members in spans:
                key = (
                    ("human_1", "human_1")
                    if use.resource_type == "HUMAN"
                    else devices[use.resource_id].competition_key
                )
                joint_entries.append(
                    CalendarEntry(
                        physical_key=key,
                        interval=interval,
                        use=use,
                        task_ids=members,
                        carrier_id=batch.carrier_id,
                    )
                )
            continue
        uses.extend((assignment.task_ids[0], use, False) for use in assignment.resource_uses)
    if include_fixed:
        tasks = {t.task_id: t for t in problem.logical_tasks}
        for fact in problem.fixed_executions:
            fact_members = frozenset(t.root for t in fact.task_ids)
            has_internal_stages = len({p.interval for p in fact.task_spans}) > 1
            explicit = (
                fact.scheduled_resource_spans if fact.status == "RUNNING" else fact.resource_spans
            )
            if explicit:
                for span in explicit:
                    use = span.resource
                    key = (
                        ("human_1", "human_1")
                        if use.resource_type == "HUMAN"
                        else devices[use.resource_id].competition_key
                    )
                    reservation_id = next(
                        (
                            reservation.reservation_id
                            for reservation in problem.mandatory_programs.reservations
                            if fact_members & reservation_members[reservation.reservation_id]
                            and not (
                                reservation_members[reservation.reservation_id] <= fact_members
                                and has_internal_stages
                            )
                            and key in reservation_keys[reservation.reservation_id]
                        ),
                        None,
                    )
                    target = entries if reservation_id else joint_entries
                    target.append(
                        CalendarEntry(
                            physical_key=key,
                            interval=span.interval,
                            use=use,
                            task_ids=tuple(task for task in fact.task_ids if task in tasks),
                            reservation_id=reservation_id,
                            frozen=True,
                        )
                    )
                continue
            for task in fact.task_ids:
                if task not in tasks:
                    continue
                for use in tasks[task].operation.resource_requirements:
                    if use.resource_type == "DEVICE":
                        matching_devices = [
                            devices[r]
                            for r in fact.resource_ids
                            if r in devices
                            and devices[r].physical_resource_id == use.physical_resource_id
                            and (
                                devices[r].component_id == use.component_id
                                or use.resource_id not in devices
                            )
                        ]
                        if len(matching_devices) != 1:
                            raise ValueError("冻结资源缺少明确设备选择")
                        fixed_device = matching_devices[0]
                        use = use.model_copy(
                            update={
                                "resource_id": fixed_device.device_instance_id,
                                "physical_resource_id": fixed_device.physical_resource_id,
                                "component_id": fixed_device.component_id,
                            }
                        )
                    uses.append((task, use, True))
    for task, use, frozen in uses:
        if use.resource_type == "HUMAN":
            key = ("human_1", "human_1")
        else:
            device = devices.get(use.resource_id)
            if device is None:
                raise ValueError("布局包含未映射设备")
            key = device.competition_key
        reservation_id = reservation_by_task.get((task.root, key))
        entries.append(
            CalendarEntry(
                physical_key=key,
                interval=ports[task],
                use=use,
                task_ids=shared_members.get(task, (task,)),
                reservation_id=reservation_id,
                frozen=frozen,
            )
        )
    for reservation in problem.mandatory_programs.reservations:
        matching = [e for e in entries if e.reservation_id == reservation.reservation_id]
        if not matching or any(t not in ports for t in reservation.members):
            continue
        keys = {e.physical_key for e in matching}
        if len(keys) != 1:
            raise ValueError("一个外层预约的成员选择了不同设备")
        layers = {
            entry.use.effective_layer_indices
            for entry in matching
            if entry.use.effective_layer_indices
        }
        if len(layers) > 1:
            raise ValueError("一个外层预约的成员选择了不同层位")
        entries = [e for e in entries if e.reservation_id != reservation.reservation_id]
        entries.append(
            CalendarEntry(
                physical_key=matching[0].physical_key,
                interval=Interval(
                    start_sec=min(ports[t].start_sec for t in reservation.members),
                    end_sec=max(ports[t].end_sec for t in reservation.members),
                ),
                use=matching[0].use.model_copy(
                    update={
                        "conflict_policy": reservation.conflict_policy,
                        "layer_index": (
                            next(iter(layers))[0]
                            if layers and not matching[0].use.occupied_layer_indices
                            else None
                        ),
                    }
                ),
                task_ids=reservation.members,
                reservation_id=reservation.reservation_id,
                frozen=all(e.frozen for e in matching),
            )
        )
    return tuple(entries + joint_entries)


def conflicts(left: CalendarEntry, right: CalendarEntry) -> bool:
    if left.physical_key != right.physical_key or not left.interval.overlaps(right.interval):
        return False
    if left.reservation_id is not None and left.reservation_id == right.reservation_id:
        return False
    if set(left.use.effective_layer_indices) & set(right.use.effective_layer_indices):
        return True
    if left.use.conflict_policy in {
        None,
        "UNARY",
        "BATCH_EXCLUSIVE",
    } or right.use.conflict_policy in {None, "UNARY", "BATCH_EXCLUSIVE"}:
        return True
    a = {v.parameter: v.value for v in left.use.configuration if v.parameter != "duration_sec"}
    b = {v.parameter: v.value for v in right.use.configuration if v.parameter != "duration_sec"}
    return any(a[name] != b[name] for name in a.keys() & b.keys())


def capacity_collision(
    problem: SchedulingProblem, entries: tuple[CalendarEntry, ...] | list[CalendarEntry]
) -> tuple[CalendarEntry, CalendarEntry] | None:
    """多层腔体配置保持一致，固定层集合和动态单层都按实际用量占用。"""
    for device in problem.resources:
        if device.capacity <= 1:
            continue
        values = [entry for entry in entries if entry.physical_key == device.competition_key]
        points = sorted(
            {point for e in values for point in (e.interval.start_sec, e.interval.end_sec)}
        )
        for start, end in zip(points, points[1:], strict=False):
            active = [
                e for e in values if e.interval.start_sec <= start and e.interval.end_sec >= end
            ]
            for i, left in enumerate(active):
                a = {
                    v.parameter: v.value
                    for v in left.use.configuration
                    if v.parameter != "duration_sec"
                }
                for right in active[i + 1 :]:
                    b = {
                        v.parameter: v.value
                        for v in right.use.configuration
                        if v.parameter != "duration_sec"
                    }
                    if not {"temperature_c", "mode"} <= a.keys() or a != b:
                        return left, right
            if sum(e.use.units for e in active) > device.capacity:
                return active[0], active[-1]
    return None
