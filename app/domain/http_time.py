"""原始时刻保留在请求归档；业务偏移按会话原点向下对齐整数秒。"""

from datetime import datetime

from app.domain.time import TimeOrigin


def business_time(origin: TimeOrigin, observed_at: datetime) -> datetime:
    delta = observed_at - origin.start_at
    if delta.total_seconds() < 0:
        raise ValueError("反馈早于会话原点")
    return origin.at(delta.days * 86_400 + delta.seconds)
