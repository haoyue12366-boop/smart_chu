import json

import pytest
from pydantic import ValidationError

from app.domain.events import RuntimeEvent
from app.domain.policy import SchedulingPolicy
from app.domain.reports import SolveResult
from app.domain.resources import ResourceUse
from app.domain.runtime_snapshot import DeviceState
from app.domain.scheduling_problem import SchedulingProblem
from tests.helpers import problem


def test_immutable_problem_hash():
    value = problem()
    restored = SchedulingProblem.model_validate_json(value.model_dump_json())
    assert restored.problem_hash == value.problem_hash
    with pytest.raises(ValidationError):
        value.horizon_sec = 100
    for section, key, replacement in [
        ("policy", "policy_version", "v2"),
        ("runtime", "state_revision", 1),
    ]:
        payload = json.loads(value.model_dump_json())
        payload[section][key] = replacement
        assert SchedulingProblem.model_validate(payload).problem_hash != value.problem_hash
    payload = json.loads(value.model_dump_json())
    payload["recipe_instances"][0]["recipe_id"] = "another"
    assert SchedulingProblem.model_validate(payload).problem_hash != value.problem_hash


@pytest.mark.parametrize("status", ["UNKNOWN", "INFEASIBLE", "MODEL_INVALID"])
def test_failure_has_no_candidate(status):
    result = SolveResult(status=status, problem_hash=problem().problem_hash)
    assert result.candidate is None
    with pytest.raises(ValidationError):
        SolveResult(
            status=status,
            problem_hash=problem().problem_hash,
            candidate={"problem_hash": problem().problem_hash, "assignments": []},
        )


def test_feasible_requires_candidate():
    with pytest.raises(ValidationError):
        SolveResult(status="FEASIBLE", problem_hash=problem().problem_hash)


@pytest.mark.parametrize("count", [0, 2, True, 1.0, "1"])
def test_single_human_configuration(count):
    with pytest.raises(ValidationError):
        SchedulingPolicy(policy_version="v1", human_count=count)


def test_second_human_rejected():
    with pytest.raises(ValidationError):
        ResourceUse(resource_type="HUMAN", resource_id="human_2", conflict_policy="UNARY")


def test_event_requirements_and_failure_release():
    event = {
        "event_id": "evt-1",
        "session_id": "session-test",
        "event_type": "DEVICE_UNAVAILABLE",
        "occurred_at": "2026-09-22T12:00:00+08:00",
        "received_at": "2026-09-22T12:00:01+08:00",
        "source": "MANUAL_CONFIRM",
        "expected_state_revision": 0,
        "base_plan_version": 0,
        "payload": {"device_id": "oven_1", "reason": "设备停止", "expected_recovery_at": None},
    }
    assert RuntimeEvent.model_validate(event).payload.expected_recovery_at is None
    for required in ("event_id", "source", "occurred_at", "expected_state_revision"):
        with pytest.raises(ValidationError):
            RuntimeEvent.model_validate({k: v for k, v in event.items() if k != required})
    state = DeviceState(
        device_instance_id="oven_1",
        physical_resource_id="oven_1",
        component_id="chamber",
        availability_status="UNAVAILABLE",
        occupancy_status="AWAITING_RELEASE_CONFIRMATION",
        active_execution_id="exec-1",
        observed_at=event["occurred_at"],
        source="MANUAL_CONFIRM",
    )
    assert state.expected_recovery_at is None
    assert state.release_confirmation_event_id is None


def test_model_statistics_cannot_mutate_problem_and_resources_are_deeply_frozen():
    from app.domain.policy import ModelSize
    from app.domain.reports import SolverBuildReport

    value = problem()
    original_hash = value.problem_hash
    build = SolverBuildReport(
        problem_hash=original_hash,
        solver_build_id="build-1",
        objective_stage="MAKESPAN",
        solver_version="test",
        actual_model_size=ModelSize(total_variables=2),
    )
    assert build.actual_model_size.total_variables == 2
    assert value.problem_hash == original_hash
    payload = value.model_dump(mode="json")
    payload["solver_build_report"] = build.model_dump(mode="json")
    with pytest.raises(ValidationError):
        SchedulingProblem.model_validate(payload)
    with pytest.raises(ValidationError):
        value.policy.human_count = 2
