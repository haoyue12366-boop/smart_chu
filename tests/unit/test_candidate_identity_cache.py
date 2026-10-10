"""候选指纹复用不改变正文、复制身份或独立 Validator 的重新核验。"""

import weakref

import pytest

from app.domain import schedule
from app.domain.base import content_hash
from app.validation.schedule import ScheduleValidator
from tests.unit.test_schedule_validator import example


def test_unchanged_candidate_fingerprint_is_computed_once(monkeypatch):
    _, _, _, candidate = example()
    expected = content_hash(candidate)
    calls = []

    def observe(value):
        calls.append(value)
        return content_hash(value)

    monkeypatch.setattr(schedule, "content_hash", observe)
    assert [candidate.candidate_hash for _ in range(5)] == [expected] * 5
    assert len(calls) == 1, "重复编码同一完整候选"


@pytest.mark.parametrize("change", ["identity", "assignments", "deep-copy"])
def test_copies_and_json_roundtrip_do_not_inherit_an_old_fingerprint(change):
    _, _, _, candidate = example()
    original = candidate.candidate_hash
    body = candidate.model_dump_json()
    if change == "identity":
        copied = candidate.model_copy(update={"problem_hash": "0" * 64})
    elif change == "assignments":
        copied = candidate.model_copy(update={"assignments": ()})
    else:
        copied = candidate.model_copy(deep=True)
    assert copied.candidate_hash == content_hash(copied)
    assert (copied.candidate_hash == original) == (change == "deep-copy")
    restored = schedule.CandidateSchedule.model_validate_json(body)
    assert restored == candidate and restored.candidate_hash == original
    assert candidate.model_dump_json() == body


def test_independent_validation_rechecks_candidate_contents_after_cached_fingerprint():
    knowledge, runtime, problem, candidate = example()
    original = candidate.candidate_hash
    # 合成反例故意绕过 frozen；独立校验不能相信调用者留下的旧指纹。
    object.__setattr__(candidate, "assignments", ())
    proof = ScheduleValidator().validate(knowledge, runtime, problem, candidate)
    assert not proof.valid
    assert proof.candidate_hash == content_hash(candidate) != original
    assert "COVERAGE" in {item.code for item in proof.violations}


def test_fingerprint_cache_does_not_keep_candidate_alive():
    _, _, _, candidate = example()
    _ = candidate.candidate_hash
    pointer = weakref.ref(candidate)
    del candidate
    assert pointer() is None
