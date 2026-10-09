"""持久化会话及内部运行服务契约。"""

from typing import Literal

from pydantic import AwareDatetime, Field

from app.domain.base import FrozenModel, NonEmpty, NonNegativeInt
from app.domain.ids import TaskId
from app.domain.inventory import InventoryRule as InventoryRule
from app.domain.policy import SchedulingPolicy
from app.domain.quantity import Unit
from app.domain.recovery import ResumeProcedure
from app.domain.runtime_clock import ScheduleClockState
from app.domain.runtime_facts import RationalAmount, TaskSpan
from app.domain.runtime_snapshot import RuntimeSnapshot
from app.domain.schedule import ScheduledAssignment
from app.domain.scheduling_problem import CandidateCarrier, RecipeInstance
from app.domain.thermal import TaskReservation


class ExecutionBinding(FrozenModel):
    assignment: ScheduledAssignment
    carrier: CandidateCarrier
    plan_version: NonNegativeInt
    task_spans: tuple[TaskSpan, ...]
    continuities: tuple[TaskReservation, ...] = Field(
        default=(), exclude_if=lambda value: not value
    )


class LedgerEntry(FrozenModel):
    entry_id: NonEmpty
    event_id: NonEmpty
    execution_id: NonEmpty | None = None
    lot_id: NonEmpty
    kind: Literal["CONSUME", "PRODUCE", "LOSS", "ADJUST", "RESERVE", "RELEASE"]
    before: RationalAmount
    after: RationalAmount
    evidence_refs: tuple[NonEmpty, ...] = ()


class Allocation(FrozenModel):
    allocation_id: NonEmpty
    lot_id: NonEmpty
    task_id: TaskId
    plan_version: NonNegativeInt
    amount: RationalAmount
    status: Literal["RESERVED", "CONSUMED", "CANCELLED"] = "RESERVED"
    consumed: RationalAmount = RationalAmount(numerator=0)
    reservation_id: NonEmpty | None = None


class FutureAllocation(FrozenModel):
    """计划供给不是真实库存；只有实际形成的批次才可绑定 Allocation。"""

    reservation_id: NonEmpty
    supply_id: NonEmpty
    demand_id: NonEmpty
    task_id: TaskId
    plan_version: NonNegativeInt
    amount: RationalAmount
    unit: Unit | None = None
    status: Literal["PLANNED", "FULFILLED", "CANCELLED"] = "PLANNED"
    inventory_fulfillment_id: NonEmpty | None = None
    eligible_lot_ids: tuple[NonEmpty, ...] = ()


class RecoveryRule(FrozenModel):
    rule_id: NonEmpty
    task_ids: tuple[TaskId, ...]
    kind: Literal["REMAKE", "RESUME"]
    evidence_refs: tuple[NonEmpty, ...]
    knowledge_version: NonEmpty
    approved: Literal[True]
    source_kind: Literal["REVIEWED", "SYNTHETIC"]
    remaining_sec: NonNegativeInt | None = None
    resume_procedure: ResumeProcedure | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


class RuntimeSession(FrozenModel):
    knowledge_release_id: NonEmpty | None = None
    schema_version: Literal["p4-session-v1"] = "p4-session-v1"
    runtime: RuntimeSnapshot
    policy: SchedulingPolicy
    menu: tuple[RecipeInstance, ...] = ()
    status: Literal["CREATED", "ACTIVE", "ENDED"] = "CREATED"
    bindings: tuple[ExecutionBinding, ...] = ()
    ledger: tuple[LedgerEntry, ...] = ()
    allocations: tuple[Allocation, ...] = ()
    future_allocations: tuple[FutureAllocation, ...] = ()
    recovery_rules: tuple[RecoveryRule, ...] = ()
    inventory_rules: tuple[InventoryRule, ...] = ()
    requires_replan: bool = False
    replan_reasons: tuple[NonEmpty, ...] = ()
    dispatch_blocked: bool = True
    last_planning_failure: NonEmpty | None = None
    schedule_clock: ScheduleClockState | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


class NotificationRecord(FrozenModel):
    kind: Literal["START", "EXPECTED_END", "OVERDUE"] = "START"
    notification_id: NonEmpty
    session_id: NonEmpty
    plan_version: NonNegativeInt
    deduplication_key: NonEmpty
    trigger_at: AwareDatetime
    text: NonEmpty
    status: Literal["PENDING", "SENT", "CANCELLED"] = "PENDING"
    task_ids: tuple[TaskId, ...] = ()
    sent_at: AwareDatetime | None = None
