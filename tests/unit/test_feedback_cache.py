"""发布身份与几何相同仍应区分；缓存压缩不能改变完整事实重放。"""

import time

import pytest

from app.compiler.compiler import ProblemCompiler
from app.domain.ports import Deadline
from app.domain.reports import PlanningResult
from app.domain.schedule import ValidatedSchedule
from app.scheduling.greedy import GreedyScheduler
from app.validation.schedule import ScheduleValidator
from benchmarks.robustness.feedback_cache import BoundedMemo, ObservedDiskCache
from benchmarks.robustness.feedback_planner import binding_key
from benchmarks.robustness.observed_session import bind_plan
from benchmarks.robustness.runner import ObservedPlanner, freeze
from tests.integration.test_robustness_dispatch import witness


def test_same_geometry_with_new_publication_has_a_distinct_reference_key():
    _, session, _, _ = witness()
    republished = session.model_copy(
        update={
            "runtime": session.runtime.model_copy(
                update={
                    "current_plan_version": session.runtime.current_plan_version + 1,
                    "current_plan_ref": "synthetic-new-publication-with-same-geometry",
                }
            )
        }
    )
    assert session.bindings == republished.bindings
    assert binding_key(session) != binding_key(republished)


def test_bounded_memo_keeps_recently_read_values_and_evicts_oldest():
    cache = BoundedMemo(2)
    cache["first"], cache["second"] = 1, 2
    assert cache["first"] == 1
    cache["third"] = 3
    assert set(cache) == {"first", "third"}


def test_cold_observed_cache_restores_exact_publication_and_rejects_changed_evidence(tmp_path):
    knowledge, observed, _, _ = witness()
    deadline = Deadline(expires_at_ns=time.monotonic_ns() + 4_200_000_000)
    problem = ProblemCompiler().compile(
        knowledge, observed.menu, observed.runtime, observed.policy, deadline
    )
    candidate = GreedyScheduler().solve(problem, deadline).candidate
    proof = ScheduleValidator().validate(knowledge, observed.runtime, problem, candidate)
    assert proof.valid
    updated = bind_plan(observed, problem, ValidatedSchedule(candidate=candidate, validation=proof))
    result = PlanningResult(status="VALIDATED", candidate=candidate, validation=proof)
    key = ObservedPlanner.cache_key(knowledge, observed)
    path = tmp_path / f"replan-{key}.json.gz"
    measurement = {"failure": None, "compute_elapsed_ms": 10, "budget_ms": 2400}
    freeze(
        path,
        {
            "observed": observed.model_dump(mode="json"),
            "problem": problem.model_dump(mode="json"),
            "result": result.model_dump(mode="json"),
            "measurement": measurement,
        },
    )
    cache = ObservedDiskCache(tmp_path, limit=1)
    cache[key] = updated, problem, measurement
    other = "f" * 64
    freeze(tmp_path / f"replan-{other}.json.gz", {"synthetic": "not read"})
    cache[other] = None, None, {"failure": "explicit synthetic failure"}
    assert key not in cache.hot and len(cache.hot) == 1
    assert cache[key] == (updated, problem, measurement)
    assert len(cache.hot) == 1 and len(cache) == 2
    path.write_bytes(path.read_bytes() + b"synthetic-corruption")
    with pytest.raises(ValueError, match="身份发生变化"):
        cache.artifact(key)
