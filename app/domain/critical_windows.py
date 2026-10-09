"""显式可选执行策略；工艺原边不变，策略边单独保留来源。"""

from app.domain.candidates import stable_id
from app.domain.ids import TaskId
from app.domain.knowledge import MenuKnowledgeView
from app.domain.policy import SchedulingPolicy
from app.domain.scheduling_problem import RecipeInstance, TaskDependency


def recipe_sequence_dependencies(
    menu: tuple[RecipeInstance, ...],
    knowledge: MenuKnowledgeView,
    policy: SchedulingPolicy,
) -> tuple[TaskDependency, ...]:
    """菜单顺序中，前菜的全部汇点先于后菜的全部源点，限制为一道展开。"""
    if policy.critical_window_policy_id == "NONE":
        return ()
    recipes = {r.recipe_id: r for r in knowledge.recipes}
    edges = []
    for previous, following in zip(menu, menu[1:], strict=False):
        left, right = recipes[previous.recipe_id], recipes[following.recipe_id]
        outgoing = {d.predecessor_id for d in left.dependencies}
        incoming = {d.successor_id for d in right.dependencies}
        for end in left.operations:
            if end.operation_id in outgoing:
                continue
            for start in right.operations:
                if start.operation_id in incoming:
                    continue
                edges.append(
                    TaskDependency(
                        predecessor_id=TaskId(
                            stable_id(
                                "task", previous.recipe_instance_id.root, end.operation_id.root
                            )
                        ),
                        successor_id=TaskId(
                            stable_id(
                                "task", following.recipe_instance_id.root, start.operation_id.root
                            )
                        ),
                        evidence_refs=("policy:SERIAL_RECIPE_V1",),
                    )
                )
    return tuple(edges)
