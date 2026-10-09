"""从固定知识与运行快照完成编译、求解、验证；此处不发布数据库事务。"""

import time

from app.compiler.compiler import ProblemCompiler
from app.domain.knowledge import MenuKnowledgeView
from app.domain.ports import CpSatScheduler as SolverPort
from app.domain.ports import Deadline, PlanningRequest
from app.domain.reports import CompilationFailure, PhaseTiming, PlanningFailure, PlanningResult
from app.domain.runtime_snapshot import RuntimeSnapshot
from app.domain.scheduling_problem import SchedulingProblem
from app.scheduling.budget import ComputationBudget
from app.scheduling.engine import PlanningEngine
from app.services.preparation_budget import PreparationBudget
from app.validation.schedule import ScheduleValidator


class PlanningCore:
    def __init__(
        self, *, solver: SolverPort, computation_budget: ComputationBudget | None = None
    ) -> None:
        self.compiler = ProblemCompiler()
        self.engine = PlanningEngine(
            validator=ScheduleValidator(), solver=solver, computation_budget=computation_budget
        )
        self.last_problem: SchedulingProblem | None = None

    def compute(
        self,
        request: PlanningRequest,
        knowledge: MenuKnowledgeView,
        runtime: RuntimeSnapshot,
        deadline: Deadline,
        *,
        preparation_budget: PreparationBudget | None = None,
    ) -> PlanningResult:
        started = time.monotonic_ns()
        self.last_problem = None
        preparation_budget = preparation_budget or PreparationBudget()
        budget = (
            request.policy.replan_budget
            if runtime.details and runtime.details.planning_kind == "REPLAN"
            else request.policy.initial_budget
        )
        with preparation_budget.measure(budget.compilation_limit_ms) as compile_limit:
            problem = self.compiler.compile(
                knowledge, request.menu, runtime, request.policy, compile_limit
            )
        deadline = preparation_budget.extend(deadline)
        compilation = PhaseTiming(
            stage="COMPILATION", elapsed_ms=(time.monotonic_ns() - started) // 1_000_000
        )
        if isinstance(problem, CompilationFailure):
            result = PlanningResult(
                status="FAILED",
                failure=PlanningFailure(
                    code=problem.code,
                    failure_class=problem.failure_class,
                    message=problem.message,
                    evidence_refs=problem.evidence_refs,
                ),
            )
        else:
            self.last_problem = problem
            result = self.engine.plan(problem, knowledge, runtime, deadline)
        if time.monotonic_ns() >= deadline.expires_at_ns and result.status == "VALIDATED":
            result = PlanningResult(
                status="FAILED",
                failure=PlanningFailure(
                    code="NO_FEASIBLE_PLAN",
                    failure_class="NO_SOLUTION_WITHIN_BUDGET",
                    message="最终结果超过请求截止时间，未返回成功计划",
                ),
            )
        total = PhaseTiming(
            stage="PLANNING_CORE", elapsed_ms=(time.monotonic_ns() - started) // 1_000_000
        )
        return result.model_copy(
            update={
                "timings": (compilation, *result.timings, total),
                "first_validated_candidate_ms": (
                    result.first_validated_candidate_ms + compilation.elapsed_ms
                    if result.first_validated_candidate_ms is not None
                    else None
                ),
            }
        )
