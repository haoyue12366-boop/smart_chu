"""P6 独立可行见证与矛盾变体；全部为显式合成，逐类至少 200 样本。"""

import json
import time
from collections import Counter
from itertools import product

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.compiler.compiler import ProblemCompiler
from app.compiler.pruning import deduplicate
from app.domain.base import content_hash
from app.domain.canonical_recipe import CanonicalRecipeModel
from app.domain.events import RuntimeEvent
from app.domain.runtime_snapshot import ExecutionRecord, RuntimeSnapshot
from app.domain.schedule import (
    CandidateSchedule,
    PublishedPlan,
    ScheduledAssignment,
    ValidatedSchedule,
)
from app.domain.scheduling_problem import CandidateCarrier, RecipeInstance, SchedulingProblem
from app.domain.time import Interval, TimeOrigin
from app.runtime.notification_projection import notification_records
from app.scheduling.calendars import CalendarState
from app.scheduling.metrics import compute_metrics
from app.scheduling.placement import find_earliest_feasible_placement
from app.validation.schedule import ScheduleValidator
from tests.runtime_support import ORIGIN, p4_knowledge, policy
from tests.unit.test_joint_thermal_batches import solved_batch as solved_batch

COUNTS = Counter()
PROFILE = settings(max_examples=200, deadline=None, derandomize=True, database=None)


@pytest.fixture(autouse=True)
def record_generated_samples(request, record_testsuite_property):
    before = COUNTS[request.node.name]
    yield
    count = COUNTS[request.node.name] - before
    record_testsuite_property("p6_property:" + request.node.name, count)
    assert count >= 200, "实际生成样本不足，不以 max_examples 配置冒充执行数量"


@st.composite
def chain_inputs(draw):
    size = draw(st.integers(2, 6))
    durations = draw(st.lists(st.integers(5, 180), min_size=size, max_size=size))
    amount = draw(st.integers(1, 10000))
    offset = draw(st.integers(0, 2000))
    return durations, amount, offset


def witness(inputs, *, with_materials=True):
    durations, amount, offset = inputs

    def material(identity, spec):
        return {
            "requirement_id": identity,
            "spec_id": spec,
            "quantity_kind": "EXACT",
            "quantity": {"value": amount, "unit": "g", "scale": 1},
            "provenance_refs": ["synthetic:p6-quantity"],
        }

    operations = tuple(
        {
            "operation_id": f"op-{index}",
            "action": "CUT",
            "description": f"合成步骤{index}",
            "duration": {"execution_sec": duration},
            "material_inputs": [material(f"in-{index}", f"spec-{index}")] if with_materials else [],
            "material_outputs": [material(f"out-{index}", f"spec-{index + 1}")]
            if with_materials
            else [],
            "resource_requirements": [
                {"resource_type": "HUMAN", "resource_id": "human_1", "conflict_policy": "UNARY"}
            ],
        }
        for index, duration in enumerate(durations)
    )
    recipe = CanonicalRecipeModel(
        schema_version="1.0",
        recipe_id="synthetic-p6-chain",
        recipe_version="1",
        name="P6合成数量链",
        provenance_refs=("synthetic:p6",),
        operations=operations,
        ingredient_requirements=(material("raw", "spec-0"),) if with_materials else (),
        material_specs=tuple(
            {
                "spec_id": f"spec-{i}",
                "ingredient_id": "synthetic",
                "name": f"spec-{i}",
                "state": f"phase-{i}",
            }
            for i in range(len(durations) + 1)
        )
        if with_materials
        else (),
        dependencies=tuple(
            {
                "predecessor_id": f"op-{i}",
                "successor_id": f"op-{i + 1}",
                "min_lag_sec": 0,
                "max_lag_sec": 0,
                "reason": "合成连续见证",
                "evidence_refs": ["synthetic:p6"],
            }
            for i in range(len(durations) - 1)
        ),
    )
    knowledge = p4_knowledge().model_copy(
        update={"recipes": (recipe,), "recipe_contexts": (), "rules": ()}
    )
    menu = (
        RecipeInstance(recipe_instance_id="p6-chain", recipe_id=recipe.recipe_id, name=recipe.name),
    )
    state = RuntimeSnapshot(
        session_id="p6-property",
        state_revision=0,
        current_plan_version=0,
        knowledge_version=knowledge.release.knowledge_version,
        rule_version=knowledge.release.rule_version,
        snapshot_id=knowledge.release.snapshot_id,
        time_origin=TimeOrigin(start_at=ORIGIN),
        now_offset_sec=0,
        execution_mode="SIMULATED",
    )
    problem = ProblemCompiler().compile(knowledge, menu, state, policy(), deadline=time_limit())
    assert isinstance(problem, SchedulingProblem), problem
    assignments = []
    cursor = offset
    for task, duration in zip(problem.logical_tasks, durations, strict=True):
        carrier = next(c for c in problem.standalone_candidates if c.covers == (task.task_id,))
        assignments.append(
            ScheduledAssignment(
                carrier_id=carrier.carrier_id,
                task_ids=carrier.covers,
                interval=Interval(start_sec=cursor, end_sec=cursor + duration),
                resource_uses=carrier.resource_uses,
            )
        )
        cursor += duration
    # 增大实验用显式上界以容纳整体平移；不更改工艺、依赖或资源。
    if cursor > problem.horizon_sec:
        problem = problem.model_copy(
            update={
                "horizon_sec": cursor + 1,
                "logical_tasks": tuple(
                    t.model_copy(update={"latest_end_sec": cursor + 1})
                    for t in problem.logical_tasks
                ),
            }
        )
    candidate = CandidateSchedule(problem_hash=problem.problem_hash, assignments=tuple(assignments))
    return knowledge, state, problem, candidate


def time_limit():
    from app.domain.ports import Deadline

    return Deadline(expires_at_ns=time.monotonic_ns() + 10_000_000_000)


def checked(knowledge, state, problem, candidate):
    return ScheduleValidator().validate(knowledge, state, problem, candidate)


@PROFILE
@given(chain_inputs())
def test_known_witness_preserves_coverage_quantities_and_metrics(inputs):
    knowledge, state, problem, candidate = witness(inputs)
    result = checked(knowledge, state, problem, candidate)
    assert result.valid, result.violations
    durations, _, offset = inputs
    metrics = compute_metrics(candidate, problem)
    assert metrics.makespan_sec == offset + sum(durations)
    assert metrics.total_human_work_sec == sum(durations)
    assert Counter(t for a in candidate.assignments for t in a.task_ids) == Counter(
        t.task_id for t in problem.logical_tasks
    )
    assert checked(
        knowledge, state, problem, candidate.model_copy(update={"metrics": metrics})
    ).valid
    COUNTS["test_known_witness_preserves_coverage_quantities_and_metrics"] += 1


@PROFILE
@given(chain_inputs(), st.booleans(), st.integers(0, 10000))
def test_missing_or_duplicate_required_operation_cannot_pass(inputs, duplicate, index):
    knowledge, state, problem, candidate = witness(inputs)
    values = list(candidate.assignments)
    target = index % len(values)
    values = values + [values[target]] if duplicate else values[:target] + values[target + 1 :]
    result = checked(
        knowledge, state, problem, candidate.model_copy(update={"assignments": tuple(values)})
    )
    assert not result.valid and "COVERAGE" in {v.code for v in result.violations}
    COUNTS["test_missing_or_duplicate_required_operation_cannot_pass"] += 1


@PROFILE
@given(chain_inputs(), st.integers(1, 1000), st.booleans())
def test_material_share_and_provenance_tampering_is_rejected(inputs, extra, metadata):
    knowledge, state, problem, candidate = witness(inputs)
    original = problem.material_flow.demands[0]
    changed = (
        original.model_copy(
            update={
                "requirement": original.requirement.model_copy(
                    update={"provenance_refs": ("forged",)}
                )
            }
        )
        if metadata
        else original.model_copy(update={"share_numerator": original.share_numerator + extra})
    )
    flow = problem.material_flow.model_copy(
        update={"demands": (changed, *problem.material_flow.demands[1:])}
    )
    problem = problem.model_copy(update={"material_flow": flow})
    result = checked(
        knowledge,
        state,
        problem,
        candidate.model_copy(update={"problem_hash": problem.problem_hash}),
    )
    assert not result.valid and "MATERIAL_BINDING" in {v.code for v in result.violations}
    COUNTS["test_material_share_and_provenance_tampering_is_rejected"] += 1


@PROFILE
@given(chain_inputs(), st.integers(1, 4))
def test_single_human_overlap_is_independently_rejected(inputs, overlap):
    knowledge, state, problem, candidate = witness(inputs)
    first, second, *tail = candidate.assignments
    duration = second.interval.end_sec - second.interval.start_sec
    start = first.interval.end_sec - overlap
    second = second.model_copy(
        update={"interval": Interval(start_sec=start, end_sec=start + duration)}
    )
    result = checked(
        knowledge,
        state,
        problem,
        candidate.model_copy(update={"assignments": (first, second, *tail)}),
    )
    assert not result.valid and "RESOURCE_OVERLAP" in {v.code for v in result.violations}
    COUNTS["test_single_human_overlap_is_independently_rejected"] += 1


@PROFILE
@given(st.integers(1, 700), st.integers(0, 10000))
def test_fixed_heat_cannot_be_shortened_even_with_rebound_problem(solved_batch, difference, index):
    context, problem, candidate = solved_batch
    batch = problem.thermal_batch_candidates[0]
    heat = [
        t.task_id
        for t in problem.logical_tasks
        if t.operation.action == "HEAT" and t.task_id in batch.covers
    ]
    target = heat[index % len(heat)]
    batch = batch.model_copy(
        update={
            "member_offsets": tuple(
                span.model_copy(update={"end_offset_sec": span.end_offset_sec - difference})
                if span.task_id == target
                else span
                for span in batch.member_offsets
            )
        }
    )
    problem = problem.model_copy(update={"thermal_batch_candidates": (batch,)})
    candidate = candidate.model_copy(update={"problem_hash": problem.problem_hash})
    result = checked(context.group.knowledge, context.group.runtime, problem, candidate)
    assert not result.valid and "THERMAL_BATCH" in {v.code for v in result.violations}
    COUNTS["test_fixed_heat_cannot_be_shortened_even_with_rebound_problem"] += 1


@PROFILE
@given(chain_inputs(), st.integers(1, 1000))
def test_plan_cannot_rewrite_completed_history(inputs, shift):
    knowledge, state, problem, candidate = witness(inputs, with_materials=False)
    first = candidate.assignments[0]
    execution = ExecutionRecord(
        execution_id="p6-completed",
        task_ids=first.task_ids,
        status="COMPLETED",
        source="SIMULATED",
        event_refs=("p6-completion",),
        resource_ids=("human_1",),
        started_at=state.time_origin.at(first.interval.start_sec),
        finished_at=state.time_origin.at(first.interval.end_sec),
    )
    state = state.model_copy(
        update={"executions": (execution,), "now_offset_sec": first.interval.end_sec}
    )
    problem = problem.model_copy(update={"runtime": state, "fixed_executions": (execution,)})
    repeated = first.model_copy(
        update={
            "interval": Interval(
                start_sec=first.interval.start_sec + shift, end_sec=first.interval.end_sec + shift
            )
        }
    )
    candidate = candidate.model_copy(
        update={
            "problem_hash": problem.problem_hash,
            "assignments": (repeated, *candidate.assignments[1:]),
        }
    )
    assert not checked(knowledge, state, problem, candidate).valid
    assert state.executions == (execution,)
    COUNTS["test_plan_cannot_rewrite_completed_history"] += 1


@PROFILE
@given(chain_inputs(), st.integers(1, 10000))
def test_notification_identities_bind_existing_plan_and_preserve_labels(inputs, version):
    knowledge, state, problem, candidate = witness(inputs, with_materials=False)
    report = checked(knowledge, state, problem, candidate)
    assert report.valid
    plan = PublishedPlan(
        session_id=state.session_id,
        plan_version=version,
        parent_plan_version=version - 1,
        state_revision=state.state_revision,
        knowledge_version=state.knowledge_version,
        snapshot_id=state.snapshot_id,
        time_origin=state.time_origin,
        validated=ValidatedSchedule(candidate=candidate, validation=report),
        publication_id=f"p6-publication-{version}",
        committed_at=ORIGIN,
    )
    records = notification_records(problem, plan, state)
    assert len(records) == 2 * len(inputs[0])
    assert len({r.deduplication_key for r in records}) == len(records)
    assert all(
        r.plan_version == version and r.notification_id.startswith(plan.publication_id)
        for r in records
    )
    for index, assignment in enumerate(candidate.assignments):
        notices = [r for r in records if r.task_ids == assignment.task_ids]
        assert len(notices) == 2 and all(f"合成步骤{index}" in r.text for r in notices)
        assert {state.time_origin.offset(r.trigger_at) for r in notices} == {
            assignment.interval.start_sec,
            assignment.interval.end_sec,
        }
    assert records == notification_records(problem, plan, state)
    COUNTS["test_notification_identities_bind_existing_plan_and_preserve_labels"] += 1


@PROFILE
@given(chain_inputs(), st.integers(0, 10000))
def test_serialized_knowledge_event_and_problem_replay_preserve_identity(inputs, identity):
    knowledge, state, problem, candidate = witness(inputs)
    knowledge2 = type(knowledge).model_validate_json(knowledge.model_dump_json())
    problem2 = SchedulingProblem.model_validate_json(problem.model_dump_json())
    candidate2 = CandidateSchedule.model_validate_json(candidate.model_dump_json())
    event = RuntimeEvent(
        event_id=f"p6-event-{identity}",
        session_id=state.session_id,
        event_type="ADVANCE_SIMULATION",
        occurred_at=ORIGIN,
        received_at=ORIGIN,
        source="SIMULATED",
        expected_state_revision=0,
        base_plan_version=0,
        payload={"advance_sec": 1},
    )
    event2 = RuntimeEvent.model_validate_json(
        json.dumps(event.model_dump(mode="json"), sort_keys=True)
    )
    assert event2 == event and content_hash(event2) == content_hash(event)
    assert (
        problem2.problem_hash == problem.problem_hash
        and candidate2.candidate_hash == candidate.candidate_hash
    )
    assert checked(knowledge2, state, problem2, candidate2).valid
    COUNTS["test_serialized_knowledge_event_and_problem_replay_preserve_identity"] += 1


@PROFILE
@given(st.lists(st.integers(1, 120), min_size=2, max_size=8), st.integers(1, 120))
def test_equivalent_pruning_preserves_independently_enumerated_time_vectors(durations, other):
    candidates = tuple(
        CandidateCarrier(
            carrier_id=f"synthetic-equivalent-{i}", kind="STANDALONE", covers=("a",), duration_sec=d
        )
        for i, d in enumerate(durations)
    )
    candidates += (
        CandidateCarrier(
            carrier_id="synthetic-other", kind="STANDALONE", covers=("b",), duration_sec=other
        ),
    )
    retained = deduplicate(candidates).candidates

    def vectors(values):
        options = [
            [carrier.duration_sec for carrier in values if carrier.covers[0].root == key]
            for key in ("a", "b")
        ]
        return {(a, a + b) for a, b in product(*options)}

    assert vectors(candidates) == vectors(retained)
    COUNTS["test_equivalent_pruning_preserves_independently_enumerated_time_vectors"] += 1


@PROFILE
@given(chain_inputs(), st.integers(1, 5))
def test_greedy_rollback_restores_resources_materials_and_coverage(inputs, repeats):
    knowledge, runtime, problem, candidate = witness(inputs)
    state = CalendarState(problem)
    before, before_hash = state.current, state.state_hash
    first = next(
        carrier
        for carrier in problem.standalone_candidates
        if carrier.covers == (problem.logical_tasks[0].task_id,)
    )
    for _ in range(repeats):
        placement = find_earliest_feasible_placement(first, state, problem, time_limit())
        assert placement.assignments, placement.rejection_reasons
        state.commit(placement)
        assert set(state.current.covered) == set(first.covers)
        assert state.current.entries and state.material_allocations and state.virtual_outputs
        state.rollback()
        assert state.current == before and state.state_hash == before_hash
        assert checked(knowledge, runtime, problem, candidate).valid
    COUNTS["test_greedy_rollback_restores_resources_materials_and_coverage"] += 1
