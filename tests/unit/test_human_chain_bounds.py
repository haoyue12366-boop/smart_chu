"""独立下界及合法见证保护人工目标；同时检查不可能顺序的模型规模。"""

from app.domain.objectives import ObjectiveStage
from app.domain.runtime_facts import ActualResourceSpan
from app.domain.runtime_snapshot import ExecutionRecord
from app.domain.schedule import CandidateSchedule, ScheduledAssignment
from app.domain.time import Interval
from app.scheduling.cp_sat import CpSatScheduler
from app.scheduling.greedy import GreedyScheduler
from app.scheduling.human_hint import add_human_chain_hint
from app.scheduling.model_builder import ModelBuilder
from app.scheduling.objectives import apply_stage
from app.validation.schedule import ScheduleValidator
from tests.unit.test_cp_sat_model import deadline
from tests.unit.test_prepared_greedy import prepared_menu


def test_precedence_removes_impossible_human_orders_and_keeps_exact_optimum():
    knowledge, problem = prepared_menu()
    builder = ModelBuilder(problem, deadline())
    builder.build()
    apply_stage(builder, ObjectiveStage(name="D_HUMAN", makespan_cap_sec=1500))
    count = len(builder.human_intervals)
    assert count == 4
    # 每道菜的加热在收尾之前，反向顺序永远不可能，无需生成它们。
    assert builder.sequence_arcs <= count * (count - 1) - 2
    tasks = {task.task_id: task for task in problem.logical_tasks}
    instances = {
        instance.recipe_instance_id: i for i, instance in enumerate(problem.recipe_instances)
    }
    starts = {"heat": 0, "cool": 60, "finish": 1260}
    witness = CandidateSchedule(
        problem_hash=problem.problem_hash,
        assignments=tuple(
            ScheduledAssignment(
                carrier_id=carrier.carrier_id,
                task_ids=carrier.covers,
                interval=Interval(
                    start_sec=starts[tasks[carrier.covers[0]].operation_id.root]
                    + instances[tasks[carrier.covers[0]].recipe_instance_id] * 120,
                    end_sec=starts[tasks[carrier.covers[0]].operation_id.root]
                    + instances[tasks[carrier.covers[0]].recipe_instance_id] * 120
                    + carrier.duration_sec,
                ),
                resource_uses=carrier.resource_uses,
            )
            for carrier in problem.standalone_candidates
        ),
    )
    proof = ScheduleValidator().validate(knowledge, problem.runtime, problem, witness)
    assert proof.valid, proof.violations
    result = CpSatScheduler().solve(
        problem, None, deadline(), stage=ObjectiveStage(name="D_HUMAN", makespan_cap_sec=1500)
    )
    # 必需的60秒人工加热给出下界，上面的错峰计划给出相同上界。
    assert result.status == "OPTIMAL"
    assert result.objective_value == result.best_bound == 60


def test_human_hint_keeps_positive_time_history_of_a_retired_task():
    _, problem = prepared_menu()
    human = next(
        use
        for carrier in problem.standalone_candidates
        for use in carrier.resource_uses
        if use.resource_type == "HUMAN"
    )
    record = ExecutionRecord(
        execution_id="past-failed",
        task_ids=("retired-task",),
        status="FAILED",
        source="SIMULATED",
        event_refs=("past-start", "past-fail"),
        started_at=problem.runtime.time_origin.at(90),
        finished_at=problem.runtime.time_origin.at(150),
        resource_spans=(
            ActualResourceSpan(
                resource=human,
                interval=Interval(start_sec=90, end_sec=150),
                event_refs=("past-start", "past-fail"),
            ),
        ),
    )
    problem = problem.model_copy(
        update={
            "runtime": problem.runtime.model_copy(
                update={"now_offset_sec": 200, "executions": (record,)}
            )
        }
    )
    future = GreedyScheduler().solve(problem, deadline()).candidate
    assert future is not None
    builder = ModelBuilder(problem, deadline())
    builder.build()
    apply_stage(builder, ObjectiveStage(name="D_HUMAN"))
    add_human_chain_hint(builder, future)
    hinted_names = {
        builder.model.proto.variables[index].name
        for index in builder.model.proto.solution_hint.vars
    }
    assert "max-human-busy" in hinted_names
    assert "busy-start:0" in hinted_names


def test_zero_duration_history_cannot_bridge_a_real_rest():
    knowledge, problem = prepared_menu(200)
    human = next(
        use
        for carrier in problem.standalone_candidates
        for use in carrier.resource_uses
        if use.resource_type == "HUMAN"
    )
    records = tuple(
        ExecutionRecord(
            execution_id=f"past-{index}",
            task_ids=(f"retired-{index}",),
            status="FAILED",
            source="SIMULATED",
            event_refs=(f"past-event-{index}",),
            started_at=problem.runtime.time_origin.at(start),
            finished_at=problem.runtime.time_origin.at(end),
            resource_spans=(
                ActualResourceSpan(
                    resource=human,
                    interval=Interval(start_sec=start, end_sec=end),
                    event_refs=(f"past-event-{index}",),
                ),
            ),
        )
        for index, (start, end) in enumerate(((0, 30), (60, 60), (90, 120)))
    )
    problem = problem.model_copy(
        update={"runtime": problem.runtime.model_copy(update={"executions": records})}
    )
    tasks = {task.task_id: task for task in problem.logical_tasks}
    instances = {
        instance.recipe_instance_id: index
        for index, instance in enumerate(problem.recipe_instances)
    }
    starts = {"heat": 200, "cool": 260, "finish": 1460}
    witness = CandidateSchedule(
        problem_hash=problem.problem_hash,
        assignments=tuple(
            ScheduledAssignment(
                carrier_id=carrier.carrier_id,
                task_ids=carrier.covers,
                interval=Interval(
                    start_sec=starts[tasks[carrier.covers[0]].operation_id.root]
                    + instances[tasks[carrier.covers[0]].recipe_instance_id] * 120,
                    end_sec=starts[tasks[carrier.covers[0]].operation_id.root]
                    + instances[tasks[carrier.covers[0]].recipe_instance_id] * 120
                    + carrier.duration_sec,
                ),
                resource_uses=carrier.resource_uses,
            )
            for carrier in problem.standalone_candidates
        ),
    )
    proof = ScheduleValidator().validate(knowledge, problem.runtime, problem, witness)
    assert proof.valid, proof.violations
    result = CpSatScheduler().solve(
        problem, witness, deadline(), stage=ObjectiveStage(name="D_HUMAN", makespan_cap_sec=1800)
    )
    # 历史两个30秒主动段之间有真实60秒休息；零时长记录不消耗人工。
    # 必需的未来60秒加热给出下界，显式错峰见证给出同一上界。
    assert result.status == "OPTIMAL"
    assert result.objective_value == result.best_bound == 60
