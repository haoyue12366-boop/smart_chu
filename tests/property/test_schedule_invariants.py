"""合成独立物料链整体平移和秒级长度反例。"""

from hypothesis import given, settings
from hypothesis import strategies as st

from app.domain.time import Interval
from tests.unit.test_schedule_validator import example, validate


@settings(max_examples=15, deadline=None)
@given(st.integers(min_value=1, max_value=59))
def test_every_nonzero_duration_corruption_is_rejected(extra):
    knowledge, state, problem, candidate = example()
    first, last = candidate.assignments
    last = last.model_copy(update={"interval": Interval(start_sec=60, end_sec=120 + extra)})
    candidate = candidate.model_copy(update={"assignments": (first, last)})
    assert not validate(knowledge, state, problem, candidate).valid
