"""真实归档反例：在开启关键工艺前避免后续人工被占用，不读取未来。"""

import gzip
import json
import time

import pytest

from app.compiler.compiler import ProblemCompiler
from app.domain.policy import SchedulingPolicy
from app.domain.ports import Deadline
from app.domain.reports import PlanningResult
from app.domain.runtime_session import RuntimeSession
from app.domain.schedule import ValidatedSchedule
from app.domain.scheduling_problem import SchedulingProblem
from app.scheduling.metrics import compute_metrics
from app.validation.schedule import ScheduleValidator
from benchmarks.dataset import ROOT, load_inputs
from benchmarks.robustness.observed_session import bind_plan, start_session
from benchmarks.robustness.simulator import FutureDurations, TrajectorySimulator


def archived_simulator(case, trajectory, guarded=True, duration="NOMINAL"):
    directory = ROOT / "benchmarks/reports/P6-feedback-critical-small-run2"
    archived = json.loads(
        gzip.decompress((directory / f"initial-{case}-{duration}.json.gz").read_bytes())
    )
    old = RuntimeSession.model_validate(archived["session"])
    values = old.policy.model_dump(mode="json")
    values.update(
        policy_version=old.policy.policy_version + ":tight-human-guard-v1",
        dispatch_guard_policy_id="TIGHT_HUMAN_V1" if guarded else "NONE",
    )
    selected = SchedulingPolicy.model_validate(values)
    base, _ = load_inputs()
    ids = {i.recipe_id for i in old.menu}
    knowledge = base.model_copy(
        update={
            "recipes": tuple(r for r in base.recipes if r.recipe_id in ids),
            "recipe_contexts": tuple(c for c in base.recipe_contexts if c.recipe_id in ids),
        }
    )
    session = start_session(
        knowledge,
        selected,
        {"case_id": case, "recipe_ids": [i.recipe_id.root for i in old.menu]},
        old.runtime.time_origin.start_at,
    )
    problem = ProblemCompiler().compile(
        knowledge,
        session.menu,
        session.runtime,
        selected,
        Deadline(expires_at_ns=time.monotonic_ns() + 4_200_000_000),
    )
    assert isinstance(problem, SchedulingProblem), problem
    candidate = PlanningResult.model_validate(archived["result"]).candidate
    assert candidate is not None
    candidate = candidate.model_copy(update={"problem_hash": problem.problem_hash})
    candidate = candidate.model_copy(update={"metrics": compute_metrics(candidate, problem)})
    proof = ScheduleValidator().validate(knowledge, session.runtime, problem, candidate)
    assert proof.valid
    session = bind_plan(session, problem, ValidatedSchedule(candidate=candidate, validation=proof))
    config = json.loads((ROOT / "benchmarks/scenarios/duration_profiles.json").read_bytes())
    return TrajectorySimulator(
        knowledge,
        session,
        problem,
        candidate,
        FutureDurations(config["seed"], case, trajectory, config),
        config,
    )


@pytest.mark.parametrize(
    "case,trajectory", [("combination-000", 2), ("combination-018", 0), ("combination-052", 0)]
)
def test_real_zero_gap_human_conflict_completes_with_guard(case, trajectory):
    simulation = archived_simulator(case, trajectory)
    result = simulation.run()
    assert result["status"] == "COMPLETED", result["failure"]
    assert result["validation"]["valid"]


def test_guard_off_retains_the_real_failure_as_control():
    result = archived_simulator("combination-000", 2, guarded=False).run()
    assert result["status"] == "FAILED"
    assert result["failure_detail"]["code"] == "ACTUAL_WINDOW_EXPIRED"


@pytest.mark.parametrize("trajectory", [0, 1, 3, 9])
def test_buffered_chain_is_protected_before_its_first_tight_window_opens(trajectory):
    result = archived_simulator("combination-052", trajectory, duration="BUFFERED").run()
    assert result["status"] == "COMPLETED", result["failure"]
    assert result["validation"]["valid"]
