"""问题身份重复读取不应反复序列化历史；复制和独立校验仍绑定实际内容。"""

import app.domain.scheduling_problem as contract
from app.domain.base import content_hash
from app.domain.scheduling_problem import SchedulingProblem
from app.validation.schedule import ScheduleValidator
from tests.unit.test_schedule_validator import example


def test_problem_identity_is_computed_once_per_immutable_instance(monkeypatch):
    _, _, original, _ = example()
    problem = SchedulingProblem.model_validate_json(original.model_dump_json())
    calls = []

    def counted(value):
        calls.append(value)
        return content_hash(value)

    monkeypatch.setattr(contract, "content_hash", counted)
    expected = content_hash(problem)
    assert problem.problem_hash == problem.problem_hash == expected
    assert len(calls) == 1
    changed = problem.model_copy(update={"horizon_sec": problem.horizon_sec + 60})
    assert changed.problem_hash == content_hash(changed)
    assert changed.problem_hash != expected
    assert len(calls) == 2
    assert problem.problem_hash == expected
    assert len(calls) == 2


def test_copy_and_serialization_do_not_inherit_a_cached_old_identity():
    _, _, problem, _ = example()
    before = problem.model_dump_json()
    original_hash = problem.problem_hash
    copied = problem.model_copy(update={"model_estimated_proto_bytes": 97}, deep=True)
    assert copied.problem_hash != original_hash
    assert copied.problem_hash == content_hash(copied)
    assert problem.model_dump_json() == before
    restored = SchedulingProblem.model_validate_json(before)
    assert restored.problem_hash == original_hash


def test_independent_validator_rehashes_even_if_cached_identity_is_forged():
    knowledge, runtime, problem, candidate = example()
    old_hash = problem.problem_hash
    # 明确的恶意绕过；正常调用方只能 model_copy，不能改写已冻结对象。
    object.__setattr__(problem, "horizon_sec", problem.horizon_sec + 60)
    problem.__dict__["problem_hash"] = old_hash
    report = ScheduleValidator().validate(knowledge, runtime, problem, candidate)
    assert not report.valid
    assert any(issue.code == "IDENTITY" for issue in report.violations)
