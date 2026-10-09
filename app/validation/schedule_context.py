"""一次独立扫描的上下文；只依赖领域事实，不使用编译器实现。"""

from dataclasses import dataclass, field

from app.domain.base import content_hash
from app.domain.canonical_recipe import OperationTemplate
from app.domain.ids import TaskId
from app.domain.knowledge import MenuKnowledgeView
from app.domain.runtime_snapshot import RuntimeSnapshot
from app.domain.schedule import CandidateSchedule
from app.domain.scheduling_problem import SchedulingProblem
from app.domain.time import Interval
from app.domain.validation_contract import ValidationViolation


@dataclass
class Scan:
    knowledge: MenuKnowledgeView
    runtime: RuntimeSnapshot
    problem: SchedulingProblem
    candidate: CandidateSchedule
    problem_hash: str = field(init=False)
    issues: list[ValidationViolation] = field(default_factory=list)
    operations: dict[TaskId, OperationTemplate] = field(default_factory=dict)
    intervals: dict[TaskId, Interval] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # 每次扫描从完整 IR 重新求值；仅在这次扫描内复用，不信任调用方缓存。
        self.problem_hash = content_hash(self.problem)

    def fail(self, code: str, message: str, *refs: str, evidence: tuple[str, ...] = ()) -> None:
        self.issues.append(
            ValidationViolation(
                code=code, message=message, entity_refs=refs, evidence_refs=evidence
            )
        )
