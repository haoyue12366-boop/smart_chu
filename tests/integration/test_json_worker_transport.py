"""完整消息字段/哈希回放及真实 Windows 子进程独立校验。"""

import json

import pytest
from pydantic import ValidationError

from app.domain.objectives import ObjectiveStage
from app.scheduling import json_worker
from app.scheduling.json_worker import (
    JsonSolverWorker,
    _decode_job,
    _decode_response,
    _encode_job,
    _encode_response,
)
from app.scheduling.worker import WorkerJob, WorkerResponse
from app.validation.schedule import ScheduleValidator
from tests.unit.test_cp_sat_model import deadline
from tests.unit.test_schedule_validator import example


def test_complete_job_and_response_roundtrip_preserves_content_and_identities():
    knowledge, state, problem, candidate = example()
    job = WorkerJob(
        job_id="json-job",
        problem=problem,
        hint=candidate,
        deadline=deadline(),
        serial_menu=True,
        stage=ObjectiveStage(name="A_MAKESPAN"),
    )
    decode, args = _encode_job(job)
    restored = decode(*args)
    assert restored == job
    assert restored.problem.problem_hash == problem.problem_hash
    assert restored.hint.candidate_hash == candidate.candidate_hash
    assert ScheduleValidator().validate(knowledge, state, restored.problem, restored.hint).valid
    with JsonSolverWorker() as worker:
        result = worker.solve(problem, candidate, deadline())
        response = WorkerResponse(
            job_id=job.job_id,
            worker_pid=worker.process_id,
            result=result,
            build_report=worker.last_build_report,
        )
        decode_response, response_args = _encode_response(response)
        assert decode_response(*response_args) == response
        assert result.status in {"OPTIMAL", "FEASIBLE"}
        assert result.problem_hash == problem.problem_hash
        assert ScheduleValidator().validate(knowledge, state, problem, result.candidate).valid
    assert not worker.is_alive


@pytest.mark.parametrize("decode", [_decode_job, _decode_response])
def test_invalid_json_cannot_become_ready_or_a_candidate(decode):
    with pytest.raises(ValidationError):
        decode('{"job_id":"wrong","ready":true}')


def test_cached_wire_reference_preserves_full_problem_and_rejects_missing_identity(monkeypatch):
    _, _, problem, candidate = example()
    job = WorkerJob(job_id="full-first", problem=problem, hint=candidate, deadline=deadline())
    decode, full = _encode_job(job)
    assert decode(*full) == job
    cached = job.model_copy(update={"job_id": "cached-second", "use_cached_problem": True})
    decode, small = _encode_job(cached)
    assert len(small[0]) < len(full[0]) // 2
    assert decode(*small) == cached
    altered = json.loads(small[0])
    altered["cached_problem_hash"] = "0" * 64
    with pytest.raises(ValueError):
        decode(json.dumps(altered))
    monkeypatch.setattr(json_worker, "_cached_problem", None)
    with pytest.raises(ValueError):
        decode(*small)


def test_cached_job_reuses_verified_problem_and_validates_all_new_parameters():
    _, _, problem, candidate = example()
    first = WorkerJob(job_id="verified-first", problem=problem, hint=candidate, deadline=deadline())
    decode, full = _encode_job(first)
    restored = decode(*full)
    cached = first.model_copy(update={"job_id": "reuse-second", "use_cached_problem": True})
    decode, small = _encode_job(cached)
    # 模型对象来自首次完整 JSON 核验，后续消息不应再次复制整个问题。
    repeated = decode(*small)
    assert repeated.problem is restored.problem
    assert repeated.hint == candidate
    assert repeated.deadline == cached.deadline
    altered = json.loads(small[0])
    altered["job"]["deadline"]["expires_at_ns"] = True
    with pytest.raises(ValidationError):
        decode(json.dumps(altered))
    altered = json.loads(small[0])
    altered["job"]["problem"] = {"problem_id": "replacement"}
    with pytest.raises(ValidationError):
        decode(json.dumps(altered))


def test_real_cached_json_job_and_cold_restart_are_independently_valid():
    knowledge, state, problem, candidate = example()
    with JsonSolverWorker() as worker:
        first = worker.solve(problem, candidate, deadline())
        second = worker.solve(
            problem,
            first.candidate,
            deadline(),
            stage=ObjectiveStage(name="E_QUALITY", spread_excess_cap_sec=0),
        )
        assert first.status == second.status == "OPTIMAL"
        assert ScheduleValidator().validate(knowledge, state, problem, second.candidate).valid
        worker.close()
        # 新 PID 必须重新发送完整问题，不能引用旧进程缓存。
        restarted = worker.solve(problem, candidate, deadline(10))
        assert restarted.status == "OPTIMAL"
        assert ScheduleValidator().validate(knowledge, state, problem, restarted.candidate).valid


def test_reportless_different_problem_does_not_desynchronize_wire_cache():
    knowledge, state, problem, candidate = example()
    # 完整且可解码的 B 因整数目标溢出而拒绝建模，不产生 build report。
    different = problem.model_copy(update={"horizon_sec": 1_000_000_000})
    with JsonSolverWorker() as worker:
        first = worker.solve(problem, candidate, deadline())
        original_pid = worker.process_id
        assert first.status == "OPTIMAL"
        rejected = worker.solve(different, None, deadline(), stage=ObjectiveStage(name="E_QUALITY"))
        assert rejected.status == "MODEL_INVALID"
        assert "整数范围" in rejected.diagnostic_message
        assert worker.last_build_report is None
        restored = worker.solve(
            problem, candidate, deadline(), stage=ObjectiveStage(name="E_QUALITY")
        )
        assert restored.status == "OPTIMAL"
        assert worker.process_id == original_pid
        assert worker.restart_count == 0
        assert ScheduleValidator().validate(knowledge, state, problem, restored.candidate).valid
