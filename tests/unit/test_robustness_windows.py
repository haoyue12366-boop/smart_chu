"""观察窗口边界：未知结束不是零，计划建议不覆盖硬门槛。"""

import pytest

from benchmarks.robustness.dispatch import ObservedDependency, observed_window


@pytest.mark.parametrize(
    "now,gate,end,low,high,preferred,at,code",
    [
        (120, 0, 120, 0, 0, 180, 120, None),
        (121, 0, 120, 0, 0, 180, None, "ACTUAL_WINDOW_EXPIRED"),
        (120, 0, 120, 15, 30, 195, 150, None),
        (120, 151, 120, 15, 30, 195, None, "START_WINDOW_EMPTY"),
        (120, 0, None, 0, 0, 180, None, "OBSERVATION_REQUIRED"),
        (120, 0, 120, 15, None, 100, 135, None),
    ],
)
def test_observed_window_keeps_actual_boundaries(now, gate, end, low, high, preferred, at, code):
    dependency = ObservedDependency("a", "b", end, 180, low, high)
    result = observed_window(now, gate, preferred, (dependency,))
    assert result.dispatch_at_sec == at and result.code == code


def test_known_closed_window_is_detected_even_when_another_predecessor_is_unknown():
    result = observed_window(
        121,
        0,
        180,
        (
            ObservedDependency("a", "c", 120, 180, 0, 0),
            ObservedDependency("b", "c", None, 60, 0, None),
        ),
    )
    assert result.code == "ACTUAL_WINDOW_EXPIRED"


def test_multiple_maximum_lags_use_intersection_and_minimums_use_all_predecessors():
    result = observed_window(
        90,
        0,
        180,
        (
            ObservedDependency("a", "c", 100, 180, 10, 30),
            ObservedDependency("b", "c", 105, 60, 15, 20),
        ),
    )
    assert result.earliest_start_sec == 120 and result.latest_start_sec == 125
    assert result.dispatch_at_sec == 125
