"""秒级事实到一位小数分钟的公共单位转换，不改变排程。"""

from decimal import ROUND_HALF_UP, Decimal

from app.domain.time import checked_seconds


def minute_text(seconds: int) -> str:
    checked_seconds(seconds)
    if seconds < 0:
        raise ValueError("分钟投影不接受负偏移")
    return format((Decimal(seconds) / 60).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP), ".1f")


def projected_interval(start_sec: int, end_sec: int) -> str:
    if end_sec < start_sec:
        raise ValueError("时间区间倒置")
    return f"{minute_text(start_sec)}-{minute_text(end_sec)}"
