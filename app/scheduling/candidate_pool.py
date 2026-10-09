"""候选池只持有与当前问题绑定的独立校验结果。"""

import time

from app.domain.knowledge import MenuKnowledgeView
from app.domain.objectives import ObjectiveStage
from app.domain.ports import ScheduleValidator
from app.domain.runtime_snapshot import RuntimeSnapshot
from app.domain.schedule import CandidateSchedule, ValidatedSchedule
from app.domain.scheduling_problem import SchedulingProblem
from app.domain.validation_contract import ValidationReport, ValidationViolation
from app.scheduling.layer_resources import allocate_layers
from app.scheduling.metrics import compute_metrics, objective_spread
from app.scheduling.ranking import CandidateRank, candidate_rank


class CandidatePool:
    def __init__(
        self,
        problem: SchedulingProblem,
        knowledge: MenuKnowledgeView,
        runtime: RuntimeSnapshot,
        validator: ScheduleValidator,
        previous_plan: CandidateSchedule | None = None,
    ) -> None:
        self.problem = problem
        self.knowledge = knowledge
        self.runtime = runtime
        self.validator = validator
        self.previous_plan = previous_plan
        self.values: dict[str, ValidatedSchedule] = {}
        self.rejections: list[ValidationReport] = []
        self.validation_elapsed_ns = 0
        self.max_validation_elapsed_ns = 0
        self._ranks: dict[tuple[str, bool], CandidateRank] = {}

    def add(self, candidate: CandidateSchedule, stage: ObjectiveStage | None = None) -> bool:
        started = time.monotonic_ns()
        try:
            return self._add(candidate, stage)
        finally:
            elapsed = time.monotonic_ns() - started
            self.validation_elapsed_ns += elapsed
            self.max_validation_elapsed_ns = max(self.max_validation_elapsed_ns, elapsed)

    def _add(self, candidate: CandidateSchedule, stage: ObjectiveStage | None) -> bool:
        try:
            candidate = allocate_layers(candidate, self.problem)
        except ValueError as exc:
            self.rejections.append(
                ValidationReport(
                    report_id="layer-allocation:" + candidate.candidate_hash,
                    valid=False,
                    problem_hash=self.problem.problem_hash,
                    candidate_hash=candidate.candidate_hash,
                    validator_version="layer-allocation-v1",
                    violations=(ValidationViolation(code="RESOURCE_CAPACITY", message=str(exc)),),
                )
            )
            return False
        # 未携带指标的 CP 候选先补全，再由同一次完整独立扫描核验约束和指标。
        # 原先对仅新增 metrics 的同一时间/资源计划重复扫描全部工艺，消耗发布预留。
        if candidate.metrics is None:
            try:
                candidate = candidate.model_copy(
                    update={"metrics": compute_metrics(candidate, self.problem)}
                )
            except (ValueError, KeyError) as exc:
                report = self.validator.validate(
                    self.knowledge, self.runtime, self.problem, candidate
                )
                if report.valid:
                    report = report.model_copy(
                        update={
                            "valid": False,
                            "violations": (ValidationViolation(code="METRICS", message=str(exc)),),
                        }
                    )
                self.rejections.append(report)
                return False
        report = self.validator.validate(self.knowledge, self.runtime, self.problem, candidate)
        if not report.valid:
            self.rejections.append(report)
            return False
        # 已携带的全部指标刚经独立 Validator 的时间扫描核对，不再重复
        # 调用 Solver 侧指标函数、复制整个候选并做深层模型比较。
        metrics = candidate.metrics
        assert metrics is not None
        if stage is not None:
            caps = (
                (metrics.makespan_sec, stage.makespan_cap_sec),
                (
                    max(
                        0,
                        objective_spread(metrics, self.problem)
                        - self.problem.policy.objective.spread_target_sec,
                    ),
                    stage.spread_excess_cap_sec,
                ),
                (metrics.total_human_work_sec, stage.total_human_cap_sec),
                (metrics.max_continuous_human_sec, stage.human_busy_cap_sec),
            )
            if any(cap is not None and value > cap for value, cap in caps):
                self.rejections.append(
                    report.model_copy(
                        update={
                            "valid": False,
                            "violations": (
                                ValidationViolation(
                                    code="OBJECTIVE_BOUND",
                                    message="候选超出前序目标已接受的界",
                                    evidence_refs=(stage.name,),
                                ),
                            ),
                        }
                    )
                )
                return False
        self.values[candidate.candidate_hash] = ValidatedSchedule(
            candidate=candidate, validation=report
        )
        return True

    def best(
        self, cap: int | None = None, *, makespan_first: bool = False
    ) -> ValidatedSchedule | None:
        def rank(item: tuple[str, ValidatedSchedule]) -> CandidateRank:
            identity, validated = item
            key = identity, makespan_first
            if key not in self._ranks:
                self._ranks[key] = candidate_rank(
                    validated.candidate,
                    self.problem,
                    self.previous_plan,
                    makespan_first=makespan_first,
                )
            return self._ranks[key]

        candidates = [
            (identity, v)
            for identity, v in self.values.items()
            if v.candidate.metrics is not None
            and (cap is None or v.candidate.metrics.makespan_sec <= cap)
        ]
        return min(candidates, key=rank)[1] if candidates else None
