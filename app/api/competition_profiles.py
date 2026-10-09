"""用户授权确定的项目协议；名称不代表赛方逐字段认证。"""

from typing import Literal

from app.domain.base import FrozenModel


class CompetitionProfile(FrozenModel):
    profile_version: Literal["p5-task-clock-v2"] = "p5-task-clock-v2"
    authority: Literal["USER_AUTHORIZED_PROJECT_DESIGN"] = "USER_AUTHORIZED_PROJECT_DESIGN"
    task_id_transport: Literal["QUERY_PARAMETER"] = "QUERY_PARAMETER"
    response_scope: Literal["CUMULATIVE_ACTIVE_MENU"] = "CUMULATIVE_ACTIVE_MENU"
    time_origin: Literal["SESSION_ORIGIN"] = "SESSION_ORIGIN"
    minute_precision: Literal[1] = 1
    progress_mode: Literal["SCHEDULE_CLOCK"] = "SCHEDULE_CLOCK"
