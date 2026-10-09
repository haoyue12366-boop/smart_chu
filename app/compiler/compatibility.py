"""编译器使用相同全组规则接口；索引只负责缩小候选范围。"""

from app.domain.compatibility import GroupContext
from app.domain.ports import CompatibilityDecision
from app.domain.scheduling_problem import LogicalTask
from app.knowledge.rules import RuleEngine


def evaluate_group(
    members: tuple[LogicalTask, ...], context: GroupContext
) -> CompatibilityDecision:
    return RuleEngine().evaluate_group(members, context)
