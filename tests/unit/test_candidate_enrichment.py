"""指标补全必须由完整独立校验拒绝错误，且绑定最终候选身份。"""

from app.scheduling.candidate_pool import CandidatePool
from app.scheduling.metrics import compute_metrics
from app.validation.schedule import ScheduleValidator
from tests.unit.test_schedule_validator import example


def test_unclaimed_metrics_are_enriched_and_final_identity_is_validated():
    knowledge, state, problem, candidate = example()
    candidate = candidate.model_copy(update={"metrics": None})
    pool = CandidatePool(problem, knowledge, state, ScheduleValidator())
    assert pool.add(candidate)
    chosen = pool.best()
    assert chosen.candidate.metrics.makespan_sec == 120
    assert chosen.validation.candidate_hash == chosen.candidate.candidate_hash
    assert ScheduleValidator().validate(knowledge, state, problem, chosen.candidate).valid


def test_bad_derived_metrics_cannot_turn_into_a_valid_candidate(monkeypatch):
    knowledge, state, problem, candidate = example()
    candidate = candidate.model_copy(update={"metrics": None})
    wrong = compute_metrics(candidate, problem).model_copy(update={"makespan_sec": 1})
    monkeypatch.setattr("app.scheduling.candidate_pool.compute_metrics", lambda *_: wrong)
    pool = CandidatePool(problem, knowledge, state, ScheduleValidator())
    assert not pool.add(candidate)
    assert pool.best() is None
    assert pool.rejections and not pool.rejections[0].valid


def test_incomplete_candidate_is_a_recorded_rejection_not_metric_exception():
    knowledge, state, problem, candidate = example()
    bad = candidate.model_copy(update={"assignments": candidate.assignments[:1], "metrics": None})
    pool = CandidatePool(problem, knowledge, state, ScheduleValidator())
    assert not pool.add(bad)
    assert pool.best() is None
    assert pool.rejections and not pool.rejections[0].valid
