from datetime import datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.domain.ids import RecipeId, RecipeInstanceId
from app.domain.quantity import ScaledQuantity
from app.domain.time import Interval, TimeOrigin, align_down, align_up


def test_typed_identity():
    assert RecipeId("a") != RecipeId("b")
    assert RecipeId("a") != RecipeInstanceId("a")
    with pytest.raises(ValidationError):
        RecipeId(RecipeInstanceId("a"))


def test_grid_and_interval():
    assert align_up(61, 60) == 120
    assert align_down(61, 60) == 60
    assert align_up(-61, 60) == -60
    assert align_down(-61, 60) == -120
    assert not Interval(start_sec=0, end_sec=60).overlaps(Interval(start_sec=60, end_sec=120))
    assert Interval(start_sec=0, end_sec=60).overlaps(Interval(start_sec=59, end_sec=120))
    for invalid in (True, 1.2, "60"):
        with pytest.raises((TypeError, ValueError)):
            align_up(invalid, 60)
    with pytest.raises(ValueError):
        align_down(1, 0)


def test_cross_day_and_aware_time():
    origin = TimeOrigin(start_at=datetime.fromisoformat("2026-09-22T23:59:30+08:00"))
    assert origin.at(90).isoformat() == "2026-09-23T00:01:00+08:00"
    assert origin.offset(origin.at(90000)) == 90000
    with pytest.raises(ValidationError):
        TimeOrigin(start_at=datetime(2026, 9, 22))


def test_quantity_exact_conversion():
    half = ScaledQuantity.from_decimal(Decimal("0.5"), "kg", 10)
    assert half.convert("g", 1).value == 500
    assert half.add(ScaledQuantity(value=250, unit="g", scale=1)).as_decimal() == Decimal("0.75")
    with pytest.raises(ValueError):
        ScaledQuantity.from_decimal(Decimal("0.125"), "g", 100)
    with pytest.raises(ValueError):
        half.add(ScaledQuantity(value=1, unit="ml", scale=1))


@pytest.mark.parametrize(
    "payload",
    [
        {"value": 1.5, "unit": "g", "scale": 1},
        {"value": True, "unit": "g", "scale": 1},
        {"value": 1, "unit": "???", "scale": 1},
        {"value": 1, "unit": "g", "scale": 0},
        {"value": -1, "unit": "g", "scale": 1},
        {"value": 2**63, "unit": "g", "scale": 1},
    ],
)
def test_invalid_quantities(payload):
    with pytest.raises(ValidationError):
        ScaledQuantity(**payload)


def test_elapsed_seconds_across_dst_transition():
    from zoneinfo import ZoneInfo

    origin = TimeOrigin(start_at=datetime(2026, 3, 8, 1, 30, tzinfo=ZoneInfo("America/New_York")))
    assert origin.at(3600).hour == 3
    assert origin.offset(datetime(2026, 3, 8, 3, 30, tzinfo=ZoneInfo("America/New_York"))) == 3600


def test_grid_properties():
    from hypothesis import given, settings
    from hypothesis import strategies as st

    @settings(max_examples=100, derandomize=True)
    @given(st.integers(min_value=0, max_value=10**9), st.integers(min_value=1, max_value=3600))
    def check(value, grid):
        low, high = align_down(value, grid), align_up(value, grid)
        assert low <= value <= high
        assert low % grid == high % grid == 0
        assert high - low in (0, grid)

    check()
