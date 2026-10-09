"""合成回归：中间炒制和人工休息不能让完整预约空占数小时。"""

from app.compiler.compiler import ProblemCompiler
from app.domain.ids import OperationId
from app.domain.schedule import ScheduledAssignment
from app.domain.time import Interval
from app.scheduling.heating_compaction import compact_heating_slack
from app.scheduling.metrics import compute_metrics
from app.validation.schedule import ScheduleValidator
from tests.unit.test_cp_sat_model import deadline
from tests.unit.test_reverse_cooking_hints import cooking_reservation_case


def reschedule(problem, candidate, periods):
    tasks = {t.task_id: t.operation_id.root for t in problem.logical_tasks}
    return candidate.model_copy(
        update={
            "problem_hash": problem.problem_hash,
            "metrics": None,
            "assignments": tuple(
                a.model_copy(
                    update={
                        "interval": Interval(
                            start_sec=periods[tasks[a.task_ids[0]]][0],
                            end_sec=periods[tasks[a.task_ids[0]]][1],
                        )
                    }
                )
                if tasks[a.task_ids[0]] in periods
                else a
                for a in candidate.assignments
            ),
        }
    )


def spans(problem, candidate):
    tasks = {t.task_id: t.operation_id.root for t in problem.logical_tasks}
    return {tasks[a.task_ids[0]]: a.interval for a in candidate.assignments}


def test_compacts_intermediate_reservation_without_moving_final_ready_time():
    knowledge, initial, candidate = cooking_reservation_case(tight=True)
    # 合成菜以最后拌盘为完成边界；此前的热加工预约仍必须压紧。
    context = knowledge.recipe_contexts[0]
    context = context.model_copy(
        update={
            "cooking_completion": context.cooking_completion.model_copy(
                update={"operation_ids": (OperationId("plate"),)}
            )
        }
    )
    knowledge = knowledge.model_copy(
        update={"recipe_contexts": (context, *knowledge.recipe_contexts[1:])}
    )
    problem = ProblemCompiler().compile(
        knowledge, initial.recipe_instances, initial.runtime, initial.policy, deadline()
    )
    candidate = reschedule(problem, candidate, {"unload": (1680, 1740), "plate": (1740, 1800)})
    assert ScheduleValidator().validate(knowledge, problem.runtime, problem, candidate).valid
    compacted = compact_heating_slack(candidate, problem, deadline())
    times = spans(problem, compacted)
    assert times["unload"].end_sec - times["load"].start_sec == 720
    assert times["plate"] == Interval(start_sec=1740, end_sec=1800)
    assert times["heat"].start_sec == times["load"].end_sec
    assert ScheduleValidator().validate(knowledge, problem.runtime, problem, compacted).valid


def test_compacts_heating_with_a_rest_instead_of_abandoning_the_move():
    knowledge, problem, candidate = cooking_reservation_case(tight=True)
    candidate = reschedule(
        problem,
        candidate,
        {
            "unload": (1300, 1360),
            "plate": (1360, 1420),
            "mix": (520, 700),
            "wait": (700, 2500),
            "finish": (2500, 2560),
        },
    )
    assert ScheduleValidator().validate(knowledge, problem.runtime, problem, candidate).valid
    compacted = compact_heating_slack(candidate, problem, deadline())
    times = spans(problem, compacted)
    assert times["load"] == Interval(start_sec=400, end_sec=460)
    assert times["unload"] == Interval(start_sec=1300, end_sec=1360)
    assert compute_metrics(compacted, problem).max_continuous_human_sec == 180
    assert ScheduleValidator().validate(knowledge, problem.runtime, problem, compacted).valid


def test_compaction_preserves_an_already_started_reservation():
    from app.domain.runtime_snapshot import ExecutionRecord

    knowledge, initial, candidate = cooking_reservation_case(tight=True)
    candidate = reschedule(initial, candidate, {"unload": (1680, 1740), "plate": (1740, 1800)})
    facts = tuple(
        ExecutionRecord(
            execution_id=f"fixed-preparation-{index}",
            task_ids=a.task_ids,
            status="COMPLETED",
            source="SIMULATED",
            event_refs=(f"fixed-preparation-event-{index}",),
            started_at=initial.runtime.time_origin.at(a.interval.start_sec),
            finished_at=initial.runtime.time_origin.at(a.interval.end_sec),
            resource_ids=tuple(u.resource_id for u in a.resource_uses),
        )
        for index, a in enumerate(candidate.assignments)
        if a.interval.end_sec <= 120
    )
    runtime = initial.runtime.model_copy(update={"now_offset_sec": 120, "executions": facts})
    problem = ProblemCompiler().compile(
        knowledge, initial.recipe_instances, runtime, initial.policy, deadline()
    )
    fixed = {task for fact in facts for task in fact.task_ids}
    remaining = candidate.model_copy(
        update={
            "problem_hash": problem.problem_hash,
            "assignments": tuple(a for a in candidate.assignments if not set(a.task_ids) & fixed),
        }
    )
    assert ScheduleValidator().validate(knowledge, runtime, problem, remaining).valid
    before = problem.model_dump_json()
    compacted = compact_heating_slack(remaining, problem, deadline())
    assert compacted is remaining
    assert problem.model_dump_json() == before
    assert all(a.interval.start_sec >= 120 for a in compacted.assignments)
    assert ScheduleValidator().validate(knowledge, runtime, problem, compacted).valid


def test_compacts_an_internal_tight_chain_when_the_last_steps_already_touch():
    knowledge, initial, candidate = cooking_reservation_case(tight=True)
    recipe, context = knowledge.recipes[0], knowledge.recipe_contexts[0]
    device_uses = next(
        op.resource_requirements for op in recipe.operations if op.operation_id.root == "unload"
    )
    recipe = recipe.model_copy(
        update={
            "operations": tuple(
                op.model_copy(update={"resource_requirements": device_uses})
                if op.operation_id.root == "plate"
                else op
                for op in recipe.operations
            )
        }
    )
    reservation = context.resource_reservations[0]
    context = context.model_copy(
        update={
            "cooking_completion": context.cooking_completion.model_copy(
                update={"operation_ids": (OperationId("plate"),)}
            ),
            "resource_reservations": (
                reservation.model_copy(
                    update={"members": (*reservation.members, OperationId("plate"))}
                ),
            ),
        }
    )
    knowledge = knowledge.model_copy(
        update={
            "recipes": (recipe, *knowledge.recipes[1:]),
            "recipe_contexts": (context, *knowledge.recipe_contexts[1:]),
        }
    )
    problem = ProblemCompiler().compile(
        knowledge, initial.recipe_instances, initial.runtime, initial.policy, deadline()
    )
    periods = spans(
        initial, reschedule(initial, candidate, {"unload": (1680, 1740), "plate": (1740, 1800)})
    )
    tasks = {t.task_id: t.operation_id.root for t in problem.logical_tasks}
    candidate = candidate.model_copy(
        update={
            "problem_hash": problem.problem_hash,
            "assignments": tuple(
                ScheduledAssignment(
                    carrier_id=carrier.carrier_id,
                    task_ids=carrier.covers,
                    resource_uses=carrier.resource_uses,
                    interval=periods[tasks[carrier.covers[0]]],
                )
                for carrier in problem.standalone_candidates
            ),
        }
    )
    assert ScheduleValidator().validate(knowledge, problem.runtime, problem, candidate).valid
    compacted = compact_heating_slack(candidate, problem, deadline())
    times = spans(problem, compacted)
    assert times["load"] == Interval(start_sec=1020, end_sec=1080)
    assert times["heat"] == Interval(start_sec=1080, end_sec=1680)
    assert times["plate"] == Interval(start_sec=1740, end_sec=1800)
    assert ScheduleValidator().validate(knowledge, problem.runtime, problem, compacted).valid
