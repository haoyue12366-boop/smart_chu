"""跨模块端口；实现由后继阶段提供，不在领域层装配服务。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol

from pydantic import AwareDatetime

from app.domain.base import FrozenModel, NonEmpty, NonNegativeInt
from app.domain.compatibility import GroupContext
from app.domain.competition_contract import CompetitionResponse, RecipeSelection
from app.domain.events import EventApplyResult, RuntimeEvent
from app.domain.extraction import (
    ExtractionRequest,
    ExtractionResponse,
    ExtractionRun,
    ProviderResult,
)
from app.domain.ids import RecipeId, SessionId
from app.domain.knowledge import MenuKnowledgeView, ReleaseRef, SnapshotHandle
from app.domain.objectives import ObjectiveStage
from app.domain.policy import SchedulingPolicy
from app.domain.provenance import ArtifactRef
from app.domain.reports import (
    CompilationFailure,
    DiagnosticReport,
    GreedyResult,
    PlanningResult,
    SolverBuildReport,
    SolveResult,
    ValidationReport,
)
from app.domain.runtime_snapshot import RuntimeSnapshot
from app.domain.schedule import (
    CandidateSchedule,
    PublishConflict,
    PublishContext,
    PublishedPlan,
    ValidatedSchedule,
)
from app.domain.scheduling_problem import LogicalTask, RecipeInstance, SchedulingProblem
from app.domain.time import Interval, TimeOrigin


class Deadline(FrozenModel):
    """由注入的单调时钟生成，同一链路共享，单位为纳秒。"""

    expires_at_ns: NonNegativeInt


class PlanningRequest(FrozenModel):
    request_id: NonEmpty
    menu: tuple[RecipeInstance, ...]
    policy: SchedulingPolicy


class Clock(Protocol):
    def now(self) -> datetime: ...
    def monotonic_ns(self) -> int: ...


class KnowledgeRepository(Protocol):
    def load(self, release: ReleaseRef) -> SnapshotHandle: ...
    def select(self, recipe_ids: tuple[RecipeId, ...]) -> MenuKnowledgeView: ...


class LLMProvider(Protocol):
    async def extract(self, request: ExtractionRequest) -> ExtractionResponse: ...


class ExtractionAdapter(Protocol):
    @property
    def provider_name(self) -> str: ...

    async def generate(self, rendered_request: bytes, timeout_sec: float) -> ProviderResult: ...


class ExtractionStore(Protocol):
    def put(self, payload: bytes, media_type: str) -> ArtifactRef: ...
    def read(self, ref: ArtifactRef) -> bytes: ...
    def save_run(self, run: ExtractionRun) -> ArtifactRef: ...
    def load_run(self, run_id: str) -> ExtractionRun: ...


class ProblemCompiler(Protocol):
    def compile(
        self,
        knowledge: MenuKnowledgeView,
        menu: tuple[RecipeInstance, ...],
        runtime: RuntimeSnapshot,
        policy: SchedulingPolicy,
        deadline: Deadline,
    ) -> SchedulingProblem | CompilationFailure: ...


class ScheduleValidator(Protocol):
    def validate(
        self,
        knowledge: MenuKnowledgeView,
        runtime: RuntimeSnapshot,
        problem: SchedulingProblem,
        candidate: CandidateSchedule,
    ) -> ValidationReport: ...


class GreedyScheduler(Protocol):
    def solve(self, problem: SchedulingProblem, deadline: Deadline) -> GreedyResult: ...


class CpSatScheduler(Protocol):
    def solve(
        self,
        problem: SchedulingProblem,
        hint: CandidateSchedule | None,
        deadline: Deadline,
        *,
        serial_menu: bool = False,
        stage: ObjectiveStage | None = None,
    ) -> SolveResult: ...


class PlanningEngine(Protocol):
    def plan(
        self,
        problem: SchedulingProblem,
        knowledge: MenuKnowledgeView,
        runtime: RuntimeSnapshot,
        deadline: Deadline,
    ) -> PlanningResult: ...


class PlanningCore(Protocol):
    def compute(
        self,
        request: PlanningRequest,
        knowledge: MenuKnowledgeView,
        runtime: RuntimeSnapshot,
        deadline: Deadline,
    ) -> PlanningResult: ...


class InfeasibilityAnalyzer(Protocol):
    def diagnose(
        self,
        problem: SchedulingProblem,
        build_report: SolverBuildReport,
        failure_stage: str,
        deadline: Deadline,
    ) -> DiagnosticReport: ...


class RuntimeService(Protocol):
    def apply_event(self, event: RuntimeEvent) -> EventApplyResult: ...


class PlanPublisher(Protocol):
    def publish(
        self, candidate: ValidatedSchedule, context: PublishContext
    ) -> PublishedPlan | PublishConflict: ...


class ReplanRequest(PlanningRequest):
    runtime: RuntimeSnapshot


class CompatibilityDecision(FrozenModel):
    compatible: bool
    rule_refs: tuple[NonEmpty, ...]
    evidence_refs: tuple[NonEmpty, ...]
    reasons: tuple[NonEmpty, ...] = ()
    common_configuration_keys: tuple[NonEmpty, ...] = ()


class Notification(FrozenModel):
    notification_id: NonEmpty
    session_id: SessionId
    plan_version: NonNegativeInt
    deduplication_key: NonEmpty
    trigger_at: AwareDatetime
    text: NonEmpty


class IntentResult(FrozenModel):
    status: Literal["READY", "NEEDS_CLARIFICATION", "REJECTED"]
    event: RuntimeEvent | None = None
    clarification: NonEmpty | None = None
    trace_ref: NonEmpty


class ProjectionContext(FrozenModel):
    request: tuple[RecipeSelection, ...]
    time_origin: TimeOrigin
    recipe_intervals: tuple[Interval, ...]
    device_step_indices: tuple[tuple[NonNegativeInt, NonNegativeInt], ...]


class BenchmarkSuite(FrozenModel):
    suite_id: NonEmpty
    scenario_refs: tuple[NonEmpty, ...]
    source_kind: Literal["REAL", "SYNTHETIC", "MIXED"]


class BenchmarkReport(FrozenModel):
    report_id: NonEmpty
    suite_id: NonEmpty
    knowledge_version: NonEmpty
    policy_version: NonEmpty
    seed: int
    sample_count: NonNegativeInt
    success_count: NonNegativeInt
    failure_count: NonNegativeInt
    result_artifact_refs: tuple[NonEmpty, ...]


class RuleEngine(Protocol):
    def evaluate_group(
        self, members: tuple[LogicalTask, ...], context: GroupContext
    ) -> CompatibilityDecision: ...


class ReplanningService(Protocol):
    def compute(self, request: ReplanRequest, deadline: Deadline) -> PlanningResult: ...


class NotificationService(Protocol):
    def prepare(
        self, plan: PublishedPlan, runtime: RuntimeSnapshot
    ) -> tuple[Notification, ...]: ...


class CompetitionAdapter(Protocol):
    def to_response(
        self, plan: PublishedPlan, context: ProjectionContext
    ) -> CompetitionResponse: ...


class IntentService(Protocol):
    def interpret(self, text: str, context: RuntimeSnapshot) -> IntentResult: ...


class BenchmarkRunner(Protocol):
    def run(
        self, suite: BenchmarkSuite, release: ReleaseRef, policy: SchedulingPolicy, seed: int
    ) -> BenchmarkReport: ...
