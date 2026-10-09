"""使用真实工艺与显式版本化策略边；默认不由资源争用生成菜谱先后。"""

from graphlib import CycleError, TopologicalSorter

from app.domain.base import FrozenModel
from app.domain.ids import TaskId
from app.domain.scheduling_problem import LogicalTask, TaskDependency


class DependencyGraph(FrozenModel):
    tasks: tuple[LogicalTask, ...]
    dependencies: tuple[TaskDependency, ...]
    topological_order: tuple[TaskId, ...]


def build_dependency_graph(
    tasks: tuple[LogicalTask, ...], dependencies: tuple[TaskDependency, ...]
) -> DependencyGraph:
    nodes = {task.task_id for task in tasks}
    if len(nodes) != len(tasks):
        raise ValueError("依赖图任务身份重复")
    parents: dict[TaskId, set[TaskId]] = {t.task_id: set() for t in tasks}
    for edge in dependencies:
        if edge.predecessor_id not in nodes or edge.successor_id not in nodes:
            raise ValueError("依赖边引用未知任务")
        parents[edge.successor_id].add(edge.predecessor_id)
    try:
        order = tuple(TopologicalSorter(parents).static_order())
    except CycleError as exc:
        raise ValueError("工艺依赖存在环：" + str(exc.args[1])) from exc
    return DependencyGraph(tasks=tasks, dependencies=dependencies, topological_order=order)
