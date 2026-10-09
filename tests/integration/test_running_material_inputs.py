"""合成精确投入：运行中尚未投入的部分仍是必须满足的需求。"""

import time

from app.compiler.compiler import ProblemCompiler
from app.domain.ports import Deadline
from app.domain.runtime_facts import RationalAmount
from app.domain.scheduling_problem import SchedulingProblem
from app.runtime.replanning import prepare_replan
from app.scheduling.greedy import GreedyScheduler
from app.validation.schedule import ScheduleValidator
from tests.integration.test_material_ledger import material_service
from tests.runtime_support import event


def partial_input(tmp_path):
    runtime, planning = material_service(tmp_path)
    session = runtime.get("material")
    binding = min(session.bindings, key=lambda item: item.assignment.interval.start_sec)
    lot = session.runtime.details.lots[0]
    outcome = runtime.apply_event(
        event(
            session,
            "partial-input",
            "OPERATION_STARTED",
            {
                "execution_id": "partial",
                "task_id": binding.assignment.task_ids[0],
                "consumed": [
                    {
                        "lot_id": lot.lot_id,
                        "spec_id": lot.spec_id,
                        "quantity": {"value": 3, "unit": "g", "scale": 1},
                    }
                ],
            },
        )
    )
    assert outcome.status == "APPLIED", outcome
    return runtime, planning, lot


def independent_candidate(runtime, session):
    request = prepare_replan(session, None, runtime.knowledge, session.policy)
    problem = ProblemCompiler().compile(
        runtime.knowledge,
        request.menu,
        request.runtime,
        request.policy,
        Deadline(expires_at_ns=time.monotonic_ns() + 2_000_000_000),
    )
    assert isinstance(problem, SchedulingProblem), problem
    result = GreedyScheduler().solve(
        problem, Deadline(expires_at_ns=time.monotonic_ns() + 2_000_000_000)
    )
    assert result.candidate is not None, result
    assert (
        ScheduleValidator()
        .validate(runtime.knowledge, problem.runtime, problem, result.candidate)
        .valid
    )
    return problem, result.candidate


def test_independent_validator_rejects_missing_running_input_even_without_future_assignment(
    tmp_path,
):
    runtime, _, lot = partial_input(tmp_path)
    session = runtime.get("material")
    problem, candidate = independent_candidate(runtime, session)
    facts = problem.runtime.details
    assert facts.lots[0].available.fraction() == 7
    changed_lots = tuple(
        item.model_copy(
            update={
                "available": RationalAmount(numerator=6),
                "reserved": RationalAmount(numerator=0),
            }
        )
        if item.lot_id == lot.lot_id
        else item
        for item in facts.lots
    )
    changed = problem.runtime.model_copy(
        update={"details": facts.model_copy(update={"lots": changed_lots})}
    )
    supplies = tuple(
        supply.model_copy(update={"available_share_numerator": 3, "available_share_denominator": 5})
        if supply.supply_id == lot.spec_id
        else supply
        for supply in problem.material_flow.supplies
    )
    # 同步问题身份及可用份额，隔离对尚未投入的 7g 的独立检查。
    forged = problem.model_copy(
        update={
            "runtime": changed,
            "material_flow": problem.material_flow.model_copy(update={"supplies": supplies}),
        }
    )
    candidate = candidate.model_copy(update={"problem_hash": forged.problem_hash})
    proof = ScheduleValidator().validate(runtime.knowledge, changed, forged, candidate)
    assert not proof.valid, proof
    assert any(issue.code == "RUNNING_MATERIAL_STOCK" for issue in proof.violations)


def test_running_input_replenishment_unblocks_without_restoring_consumption(tmp_path):
    runtime, planning, lot = partial_input(tmp_path)
    session = runtime.get("material")
    shortage = planning.apply_event(
        event(
            session,
            "shortage",
            "MATERIAL_SHORTAGE",
            {
                "lot_id": lot.lot_id,
                "before": {"value": 7, "unit": "g", "scale": 1},
                "after": {"value": 6, "unit": "g", "scale": 1},
                "reason": "synthetic:盘点",
                "evidence_refs": ["synthetic:盘点"],
            },
            at=10,
        )
    )
    assert shortage.event.status == "APPLIED" and shortage.status == "FAILED", shortage
    session = runtime.get("material")
    history = session.runtime.executions
    consumed = tuple(item for item in session.ledger if item.kind == "CONSUME")
    replenished = planning.apply_event(
        event(
            session,
            "replenished",
            "MATERIAL_ADJUSTED",
            {
                "lot_id": lot.lot_id,
                "before": {"value": 6, "unit": "g", "scale": 1},
                "after": {"value": 7, "unit": "g", "scale": 1},
                "reason": "synthetic:补足实物",
                "evidence_refs": ["synthetic:补足实物"],
            },
            at=10,
        )
    )
    assert replenished.status == "PUBLISHED", replenished
    after = runtime.get("material")
    assert after.runtime.executions == history
    assert tuple(item for item in after.ledger if item.kind == "CONSUME") == consumed
    assert after.runtime.details.lots[0].available.fraction() == 7
    assert not after.dispatch_blocked
