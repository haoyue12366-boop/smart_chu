"""当前状态的逐道参考：保留已发生事实、必要衔接和已开始预约的释放。"""

from app.domain.ids import TaskId
from app.domain.scheduling_problem import SchedulingProblem


def serial_task_groups(problem: SchedulingProblem) -> tuple[tuple[TaskId, ...], ...]:
    committed = {task for fact in problem.fixed_executions for task in fact.task_ids}
    committed.update(task for item in problem.fixed_task_fulfillments for task in item.task_ids)
    while True:
        extended = set(committed)
        for reservation in problem.mandatory_programs.reservations:
            if committed.intersection(reservation.members):
                # 已入炉/启热的预约须先完成并释放设备，不能被后续逐道顺序锁住。
                extended.update(reservation.members)
        for edge in problem.dependencies:
            if edge.predecessor_id in committed and edge.max_lag_sec is not None:
                extended.add(edge.successor_id)
        for relation in problem.mandatory_programs.time_relations:
            if relation.left_task in committed and relation.max_offset_sec is not None:
                extended.add(relation.right_task)
        if extended == committed:
            break
        committed = extended
    return tuple(
        group
        for instance in problem.recipe_instances
        if (
            group := tuple(
                task.task_id
                for task in problem.logical_tasks
                if task.recipe_instance_id == instance.recipe_instance_id
                and task.task_id not in committed
            )
        )
    )
