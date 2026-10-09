"""连续重排始终以当前已发布计划作为稳定性参考，不保留过时初排。"""

import hashlib
import json
from pathlib import Path

from app.domain.knowledge import MenuKnowledgeView
from app.domain.reports import PlanningResult
from app.domain.runtime_session import RuntimeSession
from app.domain.schedule import CandidateSchedule
from app.domain.scheduling_problem import SchedulingProblem
from app.scheduling.worker import SolverWorker
from benchmarks.robustness.feedback_cache import BoundedMemo, ObservedDiskCache
from benchmarks.robustness.runner import ObservedPlanner


def binding_key(session: RuntimeSession) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "publication": session.runtime.current_plan_ref,
                "version": session.runtime.current_plan_version,
                "bindings": [b.model_dump(mode="json") for b in session.bindings],
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()


class FeedbackObservedPlanner(ObservedPlanner):
    def __init__(
        self,
        worker: SolverWorker,
        previous: CandidateSchedule,
        previous_problem: SchedulingProblem,
        output: Path,
        initial: RuntimeSession,
    ) -> None:
        super().__init__(worker, previous, previous_problem, output)
        self.cache = ObservedDiskCache(output)
        self.initial_key = binding_key(initial)
        self.initial_reference = previous, previous_problem
        self.published: dict[str, str] = {}
        self.references: BoundedMemo[str, tuple[CandidateSchedule, SchedulingProblem]] = (
            BoundedMemo(16)
        )

    def __call__(
        self, knowledge: MenuKnowledgeView, observed: RuntimeSession
    ) -> tuple[RuntimeSession | None, SchedulingProblem | None, dict[str, object]]:
        bindings_key = binding_key(observed)
        if bindings_key == self.initial_key:
            self.previous, self.previous_problem = self.initial_reference
        elif bindings_key in self.references:
            self.previous, self.previous_problem = self.references[bindings_key]
        elif bindings_key in self.published:
            reference = self.cache.artifact(self.published[bindings_key])
            prior = PlanningResult.model_validate(reference["result"])
            if prior.candidate is None or reference["problem"] is None:
                raise ValueError("当前发布归档缺少完整候选和编译问题")
            self.previous = prior.candidate
            self.previous_problem = SchedulingProblem.model_validate(reference["problem"])
            self.references[bindings_key] = self.previous, self.previous_problem
        else:
            raise ValueError("缺少当前已发布计划的稳定性参考")
        updated, problem, measurement = super().__call__(knowledge, observed)
        artifact = self.cache.artifact(str(measurement["observed_key"]))
        raw_result = artifact["result"]
        result = PlanningResult.model_validate(raw_result) if raw_result else None
        measurement.update(
            selected_candidate_source=result.selected_candidate_source if result else None,
            fast_candidate_returned=bool(
                result and any(t.stage == "FEEDBACK_FEASIBILITY_RETURN" for t in result.timings)
            ),
        )
        if updated is not None:
            if problem is None:
                raise ValueError("当前发布结果缺少编译问题")
            key = binding_key(updated)
            assert result is not None and result.candidate is not None
            self.published[key] = str(measurement["observed_key"])
            self.references[key] = result.candidate, problem
        return updated, problem, measurement
