"""撤销内存试排必须同时恢复资源、覆盖与物料，保留冻结事实。"""

from hypothesis import given, settings
from hypothesis import strategies as st

from app.scheduling.calendars import CalendarState
from app.scheduling.placement import find_earliest_feasible_placement
from tests.unit.test_cp_sat_model import deadline
from tests.unit.test_schedule_validator import example


@settings(max_examples=5, deadline=None)
@given(st.integers(min_value=1, max_value=5))
def test_repeated_place_and_rollback_restores_full_state(repeats):
    _, _, problem, _ = example()
    state = CalendarState(problem)
    before = state.state_hash
    first_task = problem.logical_tasks[0].task_id
    carrier = next(c for c in problem.standalone_candidates if c.covers == (first_task,))
    for _ in range(repeats):
        placement = find_earliest_feasible_placement(carrier, state, problem, deadline())
        assert placement.assignments
        state.commit(placement)
        assert state.state_hash != before
        assert state.material_allocations
        assert state.virtual_outputs
        state.rollback()
        assert state.state_hash == before
