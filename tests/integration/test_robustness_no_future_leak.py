"""同观察前缀、不同未来仍产生相同调度输入；无扰动见证必须完整完成。"""

import json
import time

import pytest

from app.compiler.compiler import ProblemCompiler
from app.domain.base import content_hash
from app.domain.ports import Deadline
from app.domain.schedule import ValidatedSchedule
from app.scheduling.greedy import GreedyScheduler
from app.scheduling.metrics import compute_metrics
from app.validation.schedule import ScheduleValidator
from benchmarks.robustness.observed_session import bind_plan, start_session
from benchmarks.robustness.runner import CONFIG, ObservedPlanner, initial_plan, summary
from benchmarks.robustness.simulator import FutureDurations, ObservationCache, TrajectorySimulator
from tests.runtime_support import ORIGIN, policy, synthetic_knowledge


@pytest.fixture
def experiment():
    knowledge = synthetic_knowledge().model_copy(
        update={"recipes": synthetic_knowledge().recipes[:1]}
    )
    case = {"case_id": "explicit-synthetic-witness", "recipe_ids": ["synthetic-0"]}
    initial = start_session(knowledge, policy(), case, ORIGIN)
    deadline = Deadline(expires_at_ns=time.monotonic_ns() + 5_000_000_000)
    problem = ProblemCompiler().compile(
        knowledge, initial.menu, initial.runtime, initial.policy, deadline
    )
    candidate = GreedyScheduler().solve(problem, deadline).candidate
    candidate = candidate.model_copy(update={"metrics": compute_metrics(candidate, problem)})
    proof = ScheduleValidator().validate(knowledge, initial.runtime, problem, candidate)
    assert proof.valid
    session = bind_plan(initial, problem, ValidatedSchedule(candidate=candidate, validation=proof))
    return knowledge, session, problem, candidate, json.loads(CONFIG.read_bytes())


class PrivateFuture:
    def __init__(self, mix, finish):
        self.mix, self.finish = mix, finish

    def duration(self, nominal, operations):
        action = operations[0][1].action
        if action == "MARINATE":
            return nominal, False
        return (self.mix if nominal == 180 else self.finish), True


def test_unchanged_actual_durations_reproduce_complete_valid_witness(experiment):
    knowledge, session, problem, candidate, config = experiment
    result = TrajectorySimulator(
        knowledge, session, problem, candidate, PrivateFuture(180, 60), config
    ).run()
    assert result["status"] == "COMPLETED", result["failure"]
    assert result["required_task_count"] == result["completed_task_count"] == 3
    assert result["validation"]["valid"] and result["completion_sec"] == 1440
    assert result["protected_stage_count"] == 1


def test_private_future_with_identical_observations_has_identical_replan_input(experiment):
    knowledge, session, problem, candidate, config = experiment
    observed = []

    def spy(known, state):
        observed.append((content_hash(known), content_hash(state), state.model_dump_json()))
        return None, None, {"failure": "explicit test stops after recording observed prefix"}

    for remaining_future in (30, 500):
        result = TrajectorySimulator(
            knowledge,
            session,
            problem,
            candidate,
            PrivateFuture(270, remaining_future),
            config,
            planner=spy,
        ).run()
        assert result["replan_count"] == 1 and result["status"] == "FAILED"
    assert len(observed) == 2 and observed[0] == observed[1]
    assert "remaining_future" not in observed[0][2] and "common_percent" not in observed[0][2]


def test_cache_uses_complete_observed_facts_and_policy(experiment):
    knowledge, session, _, _, _ = experiment
    original = ObservedPlanner.cache_key(knowledge, session)
    changed = session.model_copy(
        update={"runtime": session.runtime.model_copy(update={"state_revision": 2})}
    )
    assert ObservedPlanner.cache_key(knowledge, changed) != original
    buffered = session.model_copy(
        update={"policy": session.policy.model_copy(update={"duration_policy_id": "BUFFERED"})}
    )
    assert ObservedPlanner.cache_key(knowledge, buffered) != original


def test_stable_local_factors_ignore_enumeration_order_and_fixed_heat(experiment):
    _, _, problem, _, config = experiment
    future = FutureDurations(20261004, "explicit-synthetic", 17, config)
    keys = [t.task_id.root for t in problem.logical_tasks]
    assert {k: future.local(k) for k in keys} == {k: future.local(k) for k in reversed(keys)}
    operation = problem.logical_tasks[0].operation
    heat = operation.model_copy(update={"action": "HEAT"})
    fixed = operation.model_copy(
        update={"duration": operation.duration.model_copy(update={"fixed_process_time": True})}
    )
    for op in (heat, fixed):
        assert future.duration(480, (("visible-template", op),)) == (480, False)
    assert future.duration(0, (("absorbed-port", operation),)) == (0, False)


def test_failures_are_not_removed_from_summary(experiment):
    knowledge, session, problem, candidate, config = experiment
    valid = TrajectorySimulator(
        knowledge, session, problem, candidate, PrivateFuture(180, 60), config
    ).run()
    failed = {"status": "FAILED", "failure": "KNOWN_SYNTHETIC_FAILURE", "replan_count": 0}
    result = summary([valid, failed])
    assert result["trajectory_count"] == 2 and result["failure_rate"] == 0.5
    assert result["spread_denominator"] == 1 and result["incomplete_separately_counted"] == 1


def test_observation_cache_reuses_only_identical_facts_and_scans_each_terminal(
    experiment, monkeypatch
):
    knowledge, session, problem, candidate, config = experiment
    cache = ObservationCache()
    scans = []
    original = ScheduleValidator.validate

    def observe_scan(self, *args, **kwargs):
        scans.append(args[2].problem_hash)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(ScheduleValidator, "validate", observe_scan)
    results = []
    for _ in range(2):
        results.append(
            TrajectorySimulator(
                knowledge, session, problem, candidate, PrivateFuture(180, 60), config, cache=cache
            ).run()
        )
    assert all(r["status"] == "COMPLETED" for r in results)
    assert results[0]["final_state_hash"] == results[1]["final_state_hash"]
    assert results[0]["events"] == results[1]["events"]
    assert len(scans) == 2 and scans[0] == scans[1]
    assert cache.hits == 6 and cache.misses == 6


def test_initial_warmup_failure_is_countable_without_fabricated_plan(experiment):
    knowledge, session, _, _, config = experiment

    class UnavailableWorker:
        process_id = None
        is_alive = False

        def warmup(self):
            return False

        def close(self):
            pass

    initial, problem, result, compute_ms, attempts = initial_plan(
        knowledge,
        session.policy,
        {"case_id": "explicit-cold-failure", "recipe_ids": ["synthetic-0"]},
        UnavailableWorker(),
        config,
        ORIGIN,
    )
    assert initial is None and problem is None
    assert result.status == "FAILED" and result.candidate is None
    assert result.failure is not None and compute_ms == 0
    assert attempts and not any(a["ready"] for a in attempts)
