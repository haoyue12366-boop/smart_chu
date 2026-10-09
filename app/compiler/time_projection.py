"""小数分钟仅为投影；六位小数足以无歧义恢复每个整数秒。"""

import re
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Literal

from app.domain.time import Interval, checked_seconds

SCALE = 1_000_000


def decimal_minutes(seconds: int) -> str:
    checked_seconds(seconds)
    if seconds < 0:
        raise ValueError("计划分钟不接受负偏移")
    micro_minutes = (seconds * SCALE + 30) // 60
    whole, fraction = divmod(micro_minutes, SCALE)
    return str(whole) if fraction == 0 else f"{whole}.{fraction:06d}".rstrip("0")


def parse_decimal_minutes(value: str) -> int:
    if not re.fullmatch(r"\d+(?:\.\d{1,6})?", value):
        raise ValueError("分钟须为非负普通小数字符串，最多六位小数")
    with localcontext() as context:
        context.prec = 60
        seconds = Decimal(value) * 60
        rounded = seconds.to_integral_value(rounding=ROUND_HALF_UP)
        if abs(seconds - rounded) > Decimal("0.00003"):
            raise ValueError("分钟值不能无损恢复为整数秒")
        return checked_seconds(int(rounded))


def project_minutes(interval: Interval, *, mode: Literal["DECIMAL", "INTEGER"] = "DECIMAL") -> str:
    if mode == "INTEGER":
        if interval.start_sec % 60 or interval.end_sec % 60:
            raise ValueError("当前计划不能无损表示为整数分钟")
        return f"{interval.start_sec // 60}-{interval.end_sec // 60}"
    if mode != "DECIMAL":
        raise ValueError("未知分钟输出模式")
    start, end = decimal_minutes(interval.start_sec), decimal_minutes(interval.end_sec)
    if (parse_decimal_minutes(start), parse_decimal_minutes(end)) != (
        interval.start_sec,
        interval.end_sec,
    ):
        raise ValueError("分钟投影回读改变计划")
    return f"{start}-{end}"
