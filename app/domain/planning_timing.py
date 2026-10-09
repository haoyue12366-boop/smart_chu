"""服务内单次排程开销；毫秒，与菜品工艺时长及客户端网络耗时分开。"""

from typing import Literal

from app.domain.base import FrozenModel, NonNegativeInt


class PlanningOverhead(FrozenModel):
    kind: Literal["INITIAL", "REPLAN"]
    elapsed_ms: NonNegativeInt
