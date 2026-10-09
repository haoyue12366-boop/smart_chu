"""剩余问题使用既有编译、求解、独立验证主链。"""

from app.domain.knowledge import MenuKnowledgeView
from app.domain.ports import CpSatScheduler, Deadline, ReplanRequest
from app.domain.reports import PlanningResult
from app.domain.schedule import CandidateSchedule
from app.domain.scheduling_problem import SchedulingProblem
from app.scheduling.budget import ComputationBudget
from app.services.planning_core import PlanningCore
from app.services.preparation_budget import PreparationBudget


class ReplanningService:
    def __init__(
        self,
        knowledge: MenuKnowledgeView,
        solver: CpSatScheduler,
        previous_plan: CandidateSchedule | None = None,
        *,
        computation_budget: ComputationBudget | None = None,
    ) -> None:
        self.knowledge = knowledge
        self.core = PlanningCore(solver=solver, computation_budget=computation_budget)
        self.core.engine.previous_plan = previous_plan

    @property
    def last_problem(self) -> SchedulingProblem | None:
        return self.core.last_problem

    def compute(
        self,
        request: ReplanRequest,
        deadline: Deadline,
        *,
        preparation_budget: PreparationBudget | None = None,
    ) -> PlanningResult:
        return self.core.compute(
            request,
            self.knowledge,
            request.runtime,
            deadline,
            preparation_budget=preparation_budget,
        )
