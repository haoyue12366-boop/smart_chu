"""应用配置只描述路径和选择；导入时不访问文件或外部服务。"""

import os
from pathlib import Path
from typing import Literal

from pydantic import Field

from app.domain.base import FrozenModel, NonEmpty

ROOT = Path(__file__).resolve().parents[1]


class AppSettings(FrozenModel):
    release_root: Path = ROOT / "data/preparations/p4-v1/releases"
    release_id: NonEmpty = "delegated-v3-multilayer-onepot-v1-all"
    policy_path: Path = ROOT / "data/policies/p6-cook-prepared-multilayer-v1.json"
    database_path: Path = ROOT / "data/runtime/p5.sqlite3"
    frontend_path: Path = ROOT / "web/dist"
    language_enabled: bool = False
    language_archive_path: Path = ROOT / "data/runtime/intent-runs"
    timezone: NonEmpty = "Asia/Shanghai"
    recovery_interval_sec: float = Field(default=0.5, gt=0)
    solver_startup_timeout_sec: float = Field(default=120, gt=0, le=600, allow_inf_nan=False)
    planning_profile: Literal["STANDARD", "RENDER"] = "STANDARD"
    scheduling_strategy: Literal["FULL_QUALITY", "FT_KITCHEN"] = "FT_KITCHEN"

    @classmethod
    def from_environment(cls) -> "AppSettings":
        fields: dict[str, object] = {}
        for name in (
            "release_root",
            "release_id",
            "policy_path",
            "database_path",
            "frontend_path",
            "language_archive_path",
            "timezone",
            "solver_startup_timeout_sec",
            "planning_profile",
            "scheduling_strategy",
        ):
            value = os.getenv("SMART_COOKING_" + name.upper())
            if value:
                fields[name] = value
        fields["language_enabled"] = os.getenv("SMART_COOKING_LANGUAGE_ENABLED", "0") == "1"
        if "planning_profile" not in fields and os.getenv("RENDER") == "true":
            fields["planning_profile"] = "RENDER"
        return cls.model_validate(fields)
