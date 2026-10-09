"""取消需求不清除尚未释放的人工占用，完成事实仍进入全会话指标。"""

import time

from app.compiler.compiler import ProblemCompiler
from app.domain.candidates import stable_id
from app.domain.ports import Deadline
from app.domain.scheduling_problem import SchedulingProblem
from app.runtime.replanning import prepare_replan
from app.scheduling.cp_sat import CpSatScheduler
from app.validation.schedule import ScheduleValidator
from tests.integration.test_menu_events_replanning import service
from tests.runtime_support import event


def running_cancelled(tmp_path):
    runtime, planning, session = service(tmp_path)
    started = planning.apply_event(
        event(
            session,
            "both",
            "START_SESSION",
            {
                "recipes": [
                    {"id": "synthetic-0", "name": "合成腌制0"},
                    {"id": "synthetic-1", "name": "合成腌制1"},
                ]
            },
        )
    )
    assert started.status == "PUBLISHED", started
    current = runtime.get("flow")
    binding = min(current.bindings, key=lambda b: b.assignment.interval.start_sec)
    task = binding.assignment.task_ids[0]
    instance = next(
        i for i in current.menu if stable_id("task", i.recipe_instance_id.root, "mix") == task.root
    )
    payload = {"task_id": task, "execution_id": "cancel-running"}
    assert (
        runtime.apply_event(event(current, "began", "OPERATION_STARTED", payload)).status
        == "APPLIED"
    )
    current = runtime.get("flow")
    cancelled = runtime.apply_event(
        event(
            current,
            "cancel",
            "CANCEL_RECIPE",
            {"recipe_instance_id": instance.recipe_instance_id},
            at=60,
        )
    )
    assert cancelled.status == "APPLIED"
    return runtime, planning, payload


def test_cancel_does_not_release_running_human_and_later_confirmation_keeps_history(tmp_path):
    runtime, planning, payload = running_cancelled(tmp_path)
    blocked = planning.drain("flow")
    assert blocked.status == "FAILED", blocked
    current = runtime.get("flow")
    assert current.runtime.executions[0].status == "RUNNING"
    assert any(o.released_at is None for o in current.runtime.details.occupancies)
    completed = planning.apply_event(
        event(current, "cancel-confirmed", "OPERATION_COMPLETED", payload, at=180)
    )
    assert completed.status == "PUBLISHED", completed
    after = runtime.get("flow")
    assert after.runtime.executions[0].status == "COMPLETED"
    assert len(after.menu) == 2
    assert len(completed.plan.validated.candidate.recipe_completions) == 1
    assert completed.plan.validated.candidate.metrics.actual_human_work_sec == 180


def test_validator_detects_live_cancelled_occupancy_when_compiler_block_is_removed(tmp_path):
    runtime, _, _ = running_cancelled(tmp_path)
    current = runtime.get("flow")
    request = prepare_replan(current, None, runtime.knowledge, current.policy)
    deadline = Deadline(expires_at_ns=time.monotonic_ns() + 2_400_000_000)
    problem = ProblemCompiler().compile(
        runtime.knowledge, request.menu, request.runtime, request.policy, deadline
    )
    assert isinstance(problem, SchedulingProblem), problem
    tampered = problem.model_copy(update={"resource_blocks": ()})
    solved = CpSatScheduler().solve(tampered, None, deadline)
    assert solved.candidate is not None
    report = ScheduleValidator().validate(
        runtime.knowledge, request.runtime, tampered, solved.candidate
    )
    assert not report.valid
    assert "STATE_RESOURCE" in {v.code for v in report.violations}
