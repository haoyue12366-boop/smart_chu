"""五分钟、总人工、总流程的优先级有独立穷举见证。"""

from app.domain.objectives import ObjectiveStage
from app.scheduling.cp_sat import CpSatScheduler
from app.scheduling.metrics import compute_metrics
from app.scheduling.model_builder import ModelBuilder
from app.scheduling.ranking import candidate_rank
from tests.unit.test_total_human_objective import deadline, enumerate_human_options, witness


def test_joint_quality_prefers_less_human_work_within_five_minutes():
    problem = witness()
    objective = problem.policy.objective.model_copy(
        update={
            "stages": ("SPREAD", "TOTAL_HUMAN_WORK", "MAKESPAN"),
            "spread_target_sec": 300,
        }
    )
    problem = problem.model_copy(
        update={"policy": problem.policy.model_copy(update={"objective": objective})}
    )
    result = CpSatScheduler().solve(
        problem, None, deadline(), stage=ObjectiveStage(name="E_QUALITY")
    )
    assert result.status == "OPTIMAL", result
    metrics = compute_metrics(result.candidate, problem)
    assert metrics.completion_spread_sec <= 300
    assert metrics.total_human_work_sec == min(v[0] for v in enumerate_human_options()) == 180
    # 两个必需被动步骤各需 240 秒，构成与优化目标独立的下界。
    assert metrics.makespan_sec == 240


def test_quality_stage_keeps_all_mandatory_tasks():
    problem = witness()
    result = CpSatScheduler().solve(
        problem, None, deadline(), stage=ObjectiveStage(name="E_QUALITY")
    )
    assert result.status == "OPTIMAL", result
    covered = [task for assignment in result.candidate.assignments for task in assignment.task_ids]
    assert len(covered) == len(set(covered)) == len(problem.logical_tasks)
    assert set(covered) == {task.task_id for task in problem.logical_tasks}


def test_proven_constant_human_work_is_removed_from_strict_objective():
    problem = witness().model_copy(update={"shared_prep_candidates": ()})
    result = CpSatScheduler().solve(
        problem, None, deadline(), stage=ObjectiveStage(name="E_QUALITY", spread_excess_cap_sec=0)
    )
    assert result.status == "OPTIMAL"
    metrics = compute_metrics(result.candidate, problem)
    assert metrics.total_human_work_sec == 240
    assert metrics.makespan_sec == 240
    assert metrics.completion_spread_sec <= problem.policy.objective.spread_target_sec
    # 人工已由完整覆盖固定为常数，目标只保留可变的真实总流程。
    assert result.objective_value == result.best_bound == metrics.makespan_sec


def test_same_problem_builds_once_without_leaking_serial_order_or_caps(monkeypatch):
    calls = []
    original = ModelBuilder.build

    def count_build(builder):
        calls.append(builder.problem.problem_hash)
        return original(builder)

    monkeypatch.setattr(ModelBuilder, "build", count_build)
    problem, solver = witness(), CpSatScheduler()
    serial = solver.solve(problem, None, deadline(), serial_menu=True)
    assert serial.status == "OPTIMAL" and serial.objective_value == 480
    quality = solver.solve(
        problem, None, deadline(), stage=ObjectiveStage(name="E_QUALITY", makespan_cap_sec=240)
    )
    assert (
        quality.status == "OPTIMAL"
        and compute_metrics(quality.candidate, problem).total_human_work_sec == 180
    )
    impossible = solver.solve(
        problem, None, deadline(), stage=ObjectiveStage(name="A_MAKESPAN", makespan_cap_sec=100)
    )
    assert impossible.status == "INFEASIBLE"
    parallel = solver.solve(problem, None, deadline(), stage=ObjectiveStage(name="A_MAKESPAN"))
    assert parallel.status == "OPTIMAL" and parallel.objective_value == 240
    assert len(calls) == 1
    changed = problem.model_copy(
        update={
            "logical_tasks": tuple(
                t.model_copy(update={"latest_end_sec": 60}) for t in problem.logical_tasks
            )
        }
    )
    assert solver.solve(changed, None, deadline()).status == "INFEASIBLE"
    assert len(calls) == 2


def test_custom_makespan_before_human_priority_is_preserved():
    problem = witness()
    standalone = problem.model_copy(update={"shared_prep_candidates": ()})
    short = CpSatScheduler().solve(standalone, None, deadline()).candidate
    assert short is not None
    short = short.model_copy(update={"problem_hash": problem.problem_hash, "metrics": None})
    shared = (
        CpSatScheduler()
        .solve(problem, None, deadline(), stage=ObjectiveStage(name="E_QUALITY"))
        .candidate
    )
    assert shared is not None
    longer = shared.model_copy(
        update={
            "assignments": tuple(
                a.model_copy(
                    update={
                        "interval": a.interval.model_copy(
                            update={
                                "start_sec": a.interval.start_sec + 100,
                                "end_sec": a.interval.end_sec + 100,
                            }
                        )
                    }
                )
                for a in shared.assignments
            ),
            "recipe_completions": tuple(
                c.model_copy(update={"completion_sec": c.completion_sec + 100})
                for c in shared.recipe_completions
            ),
            "metrics": None,
        }
    )
    assert compute_metrics(short, problem).total_human_work_sec == 240
    assert compute_metrics(longer, problem).total_human_work_sec == 180
    custom = problem.model_copy(
        update={
            "policy": problem.policy.model_copy(
                update={
                    "objective": problem.policy.objective.model_copy(
                        update={"stages": ("SPREAD", "MAKESPAN", "TOTAL_HUMAN_WORK")}
                    )
                }
            )
        }
    )
    assert candidate_rank(
        short.model_copy(update={"problem_hash": custom.problem_hash}), custom
    ) < candidate_rank(longer.model_copy(update={"problem_hash": custom.problem_hash}), custom)
    quality = custom.model_copy(
        update={
            "policy": custom.policy.model_copy(
                update={
                    "objective": custom.policy.objective.model_copy(
                        update={"stages": ("SPREAD", "TOTAL_HUMAN_WORK", "MAKESPAN")}
                    )
                }
            )
        }
    )
    assert candidate_rank(
        longer.model_copy(update={"problem_hash": quality.problem_hash}), quality
    ) < candidate_rank(short.model_copy(update={"problem_hash": quality.problem_hash}), quality)
