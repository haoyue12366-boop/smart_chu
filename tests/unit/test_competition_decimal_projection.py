"""一位小数仅投影，舍入边界与精确秒历史分别校验。"""

import pytest

from app.api.minute_projection import minute_text, projected_interval


@pytest.mark.parametrize(
    "seconds,expected",
    [(0, "0.0"), (2, "0.0"), (3, "0.1"), (30, "0.5"), (60, "1.0"), (86399, "1440.0")],
)
def test_half_up_one_decimal(seconds, expected):
    assert minute_text(seconds) == expected


def test_same_boundaries_do_not_create_display_overlap():
    assert projected_interval(1, 33) == "0.0-0.6"
    assert projected_interval(33, 61) == "0.6-1.0"
