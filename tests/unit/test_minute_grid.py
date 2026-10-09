"""用户确认的小数分钟：整数秒权威、微分钟输出、回读无秒级损失。"""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.compiler.time_projection import decimal_minutes, parse_decimal_minutes, project_minutes
from app.domain.time import Interval


@given(st.integers(min_value=0, max_value=2**60))
def test_decimal_minutes_roundtrip_every_integer_second(seconds):
    assert parse_decimal_minutes(decimal_minutes(seconds)) == seconds


def test_exact_quarter_minute_repeating_fraction_and_cross_day():
    assert decimal_minutes(15) == "0.25"
    assert decimal_minutes(20) == "0.333333"
    assert project_minutes(Interval(start_sec=15, end_sec=35)) == "0.25-0.583333"
    assert decimal_minutes(86415) == "1440.25"
    with pytest.raises(ValueError, match="整数分钟"):
        project_minutes(Interval(start_sec=0, end_sec=15), mode="INTEGER")
    assert project_minutes(Interval(start_sec=0, end_sec=120), mode="INTEGER") == "0-2"


@pytest.mark.parametrize("bad", ["NaN", "-1", "Infinity", "1e2", "0.33", "0.000001"])
def test_not_a_second_precision_minute_value_is_rejected(bad):
    with pytest.raises(ValueError):
        parse_decimal_minutes(bad)
