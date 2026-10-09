"""在出锅锚点不变时压紧设备预约内部空档，不用拖延取出来制造同步。"""

import time
from dataclasses import replace

from app.domain.carrier_timing import task_intervals
from app.domain.ids import CarrierId, TaskId
from app.domain.ports import Deadline
from app.domain.schedule import CandidateSchedule
from app.domain.scheduling_problem import SchedulingProblem
from app.domain.time import Interval
from app.scheduling.calendar_projection import entries_for, fixed_ports
from app.scheduling.calendar_types import TaskPort
from app.scheduling.calendars import CalendarState
from app.scheduling.metrics import compute_metrics
from app.scheduling.placement import find_layout_placement
from app.scheduling.ranking import candidate_rank


def compact_heating_slack(
    candidate: CandidateSchedule,
    problem: SchedulingProblem,
    deadline: Deadline,
    previous: CandidateSchedule | None = None,
) -> CandidateSchedule:
    """压紧所有尚未开始的设备预约，结果必须由调用方独立校验。"""
    if problem.policy.objective.spread_basis != "COOKING_FINISH":
        return candidate
    fixed = {port.task_id for port in fixed_ports(problem)}
    anchors = {task for boundary in problem.cooking_completions for task in boundary.task_ids}
    initial_ports = task_intervals(problem, candidate.assignments)
    reservations = []
    for reservation in problem.mandatory_programs.reservations:
        if set(reservation.members) & fixed or not set(reservation.members) <= initial_ports.keys():
            continue
        spans = sorted((initial_ports[t] for t in reservation.members), key=lambda s: s.start_sec)
        end, slack = spans[0].end_sec, 0
        for span in spans[1:]:
            slack += max(0, span.start_sec - end)
            end = max(end, span.end_sec)
        if slack:
            reservations.append((slack, reservation))
    # 有限清理预算优先处理最长空占，避免先扫描无空档预约而漏掉长等待。
    reservations.sort(key=lambda item: (-item[0], item[1].reservation_id))
    current = candidate
    now = time.monotonic_ns()
    try:
        current = _compact_preparations(
            current,
            problem,
            Deadline(expires_at_ns=now + max(0, deadline.expires_at_ns - now) // 2),
            previous,
            fixed | anchors,
            tuple(item[1].reservation_id for item in reservations),
        )
    except TimeoutError:
        return current
    for _, reservation in reservations:
        members = set(reservation.members)
        ports = task_intervals(problem, current.assignments)
        # 最终出锅时刻保持不动；中间炒制等预约以自身最后一步为界压紧。
        last_end = max(ports[task].end_sec for task in members)
        ending = members & anchors or {task for task in members if ports[task].end_sec == last_end}
        end = min(ports[task].start_sec for task in ending)
        movable = sorted(
            (a for a in current.assignments if set(a.task_ids) <= members - ending),
            key=lambda a: (-a.interval.end_sec, a.carrier_id.root),
        )
        # 预热和加热可能要求零间隔，单独移动其中一步会被另一端锁住。
        # 先收紧内部空档两侧的紧链，再尝试完整前缀，避免旧空档阻碍共同空档搜索。
        # 例如入炉→预热→加热是紧链，不能因取出前另有一步已靠紧就放弃整链。
        groups = []
        for index in range(1, len(movable)):
            if max(a.interval.end_sec for a in movable[index:]) < min(
                a.interval.start_sec for a in movable[:index]
            ):
                # 内部前段可能被后段锁住；先让后方紧链靠近取出，再跟进前段。
                groups.append(tuple(a.carrier_id for a in movable[:index]))
                groups.append(tuple(a.carrier_id for a in movable[index:]))
        groups.append(tuple(a.carrier_id for a in movable))
        if len(movable) > 1:
            groups.extend((a.carrier_id,) for a in movable)
        for group in dict.fromkeys(groups):
            if time.monotonic_ns() >= deadline.expires_at_ns:
                return current
            try:
                current = _compact_group(current, group, end, problem, deadline, previous)
            except TimeoutError:
                return current
    return current


def _compact_preparations(
    current: CandidateSchedule,
    problem: SchedulingProblem,
    deadline: Deadline,
    previous: CandidateSchedule | None,
    protected: set[TaskId],
    reservation_ids: tuple[str, ...],
) -> CandidateSchedule:
    """先收回预约前已闲置的人工备料，给长空占预约留下实际入炉空档。"""
    tasks = {task.task_id: task for task in problem.logical_tasks}
    multi_members = {
        task
        for reservation in problem.mandatory_programs.reservations
        if len(reservation.members) > 1
        for task in reservation.members
    }
    preparations = {
        task: assignment
        for assignment in current.assignments
        if not set(assignment.task_ids) & (protected | multi_members)
        and any(use.resource_type == "HUMAN" for use in assignment.resource_uses)
        and all(
            tasks[task].operation.action in {"WASH", "CUT", "MIX", "PREPARE"}
            for task in assignment.task_ids
        )
        for task in assignment.task_ids
    }
    reservations = {r.reservation_id: r for r in problem.mandatory_programs.reservations}
    initial_ports = task_intervals(problem, current.assignments)
    # 较晚预约的备料靠后，优先收回其较早占用；成功一条即交还主压紧预算。
    for reservation_id in sorted(
        reservation_ids,
        key=lambda rid: -max(initial_ports[task].end_sec for task in reservations[rid].members),
    ):
        reservation = reservations[reservation_id]
        if time.monotonic_ns() >= deadline.expires_at_ns:
            return current
        members = set(reservation.members)
        if members & protected:
            # 出锅锚点可作为界限，但任何已冻结成员都不可移动。
            if members & {port.task_id for port in fixed_ports(problem)}:
                continue
        ports = task_intervals(problem, current.assignments)
        if not members <= ports.keys():
            continue
        first_start = min(ports[task].start_sec for task in members)
        frontier = {task for task in members if ports[task].start_sec == first_start}
        pending: set[TaskId] = set()
        visited: set[TaskId] = set()
        # 上一次预算可能已收紧最靠后的备料；越过已相接部分找到首个真实空档。
        while frontier - visited:
            visited.update(frontier)
            entry_predecessors = {
                dep.predecessor_id
                for dep in problem.dependencies
                if dep.successor_id in frontier
                and dep.predecessor_id not in members
                and dep.predecessor_id in ports
            }
            first_start = min(ports[task].start_sec for task in frontier)
            ready = max((ports[task].end_sec for task in entry_predecessors), default=first_start)
            pending = {
                task
                for task in entry_predecessors
                if task in preparations
                and ports[task].end_sec == ready
                and tasks[task].recipe_instance_id == reservation.recipe_instance_id
            }
            if ready < first_start:
                break
            frontier = pending
            pending = set()
        if not pending:
            continue
        preceding: set[TaskId] = set()
        while pending:
            preceding.update(pending)
            pending = {
                dep.predecessor_id
                for dep in problem.dependencies
                if dep.successor_id in pending
                and dep.predecessor_id in preparations
                and dep.predecessor_id not in preceding
                and ports[dep.predecessor_id].end_sec == ports[dep.successor_id].start_sec
                and tasks[dep.predecessor_id].recipe_instance_id == reservation.recipe_instance_id
            }
        last_end = max(ports[task].end_sec for task in members)
        ending = members & protected or {
            task for task in members if ports[task].end_sec == last_end
        }
        end = min(ports[task].start_sec for task in ending)
        groups = [
            tuple(a.carrier_id for a in current.assignments if set(a.task_ids) <= members - ending)
        ]
        # 原本连续的人工备料整体后移，避免截止时间把同一紧链只移动一半。
        groups.append(
            tuple(
                preparations[task].carrier_id
                for task in sorted(preceding, key=lambda task: -ports[task].end_sec)
            )
        )
        for group in dict.fromkeys(groups):
            if time.monotonic_ns() >= deadline.expires_at_ns:
                return current
            try:
                before = current
                current = _compact_group(current, group, end, problem, deadline, previous)
                if group == groups[-1] and current is not before:
                    return current
            except TimeoutError:
                return current
    return current


def _compact_group(
    current: CandidateSchedule,
    group: tuple[CarrierId, ...],
    end: int,
    problem: SchedulingProblem,
    deadline: Deadline,
    previous: CandidateSchedule | None,
) -> CandidateSchedule:
    layout = tuple(a for a in current.assignments if a.carrier_id in group)
    if not layout:
        return current
    latest = end - max(a.interval.end_sec for a in layout)
    if latest <= 0:
        return current
    others = tuple(a for a in current.assignments if a.carrier_id not in group)
    calendars = CalendarState(problem)
    calendars.current = replace(
        calendars.current,
        assignments=others,
        entries=calendars.current.entries + entries_for(problem, others),
        ports=calendars.current.ports
        + tuple(
            TaskPort(task_id=task, interval=span)
            for task, span in task_intervals(problem, others).items()
        ),
    )
    # 最近空档可能恰好接上长人工段。保留真实休息后再试，不能直接放弃压紧。
    for preserve_rest in (False, True):
        if preserve_rest:
            gap = problem.policy.objective.rest_gap_sec
            calendars.current = replace(
                calendars.current,
                entries=tuple(
                    replace(
                        entry,
                        interval=Interval(
                            start_sec=max(0, entry.interval.start_sec - gap),
                            end_sec=entry.interval.end_sec + gap,
                        ),
                    )
                    if entry.use.resource_type == "HUMAN"
                    else entry
                    for entry in calendars.current.entries
                ),
            )
        placed = find_layout_placement(
            layout,
            calendars,
            problem,
            deadline,
            lock_frozen_anchor=False,
            latest_anchor_sec=latest,
        )
        if not placed.assignments or placed.assignments == layout:
            return current
        shifted = {a.carrier_id: a for a in placed.assignments}
        proposal = current.model_copy(
            update={
                "assignments": tuple(shifted.get(a.carrier_id, a) for a in current.assignments),
                "metrics": None,
                "recipe_completions": (),
            }
        )
        proposal = proposal.model_copy(update={"metrics": compute_metrics(proposal, problem)})
        if (
            candidate_rank(proposal, problem, previous)[:-1]
            <= candidate_rank(current, problem, previous)[:-1]
        ):
            return proposal
    return current
