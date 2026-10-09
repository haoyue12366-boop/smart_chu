"""语言只选择业务意图，不能输出权威排程或设备参数。"""

from typing import Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from app.domain.base import FrozenModel, NonEmpty, NonNegativeInt


class Intent(FrozenModel):
    action: Literal["ADD_RECIPE", "CANCEL_RECIPE", "DELAY_RECIPE", "CLARIFY", "UNKNOWN"]
    recipe_id: str | None = None
    recipe_instance_id: str | None = None
    earliest_start_at: AwareDatetime | None = None
    question: str | None = None
    options: tuple[str, ...] = ()

    @model_validator(mode="after")
    def required_fields(self) -> Self:
        if self.action == "ADD_RECIPE" and not self.recipe_id:
            raise ValueError("加菜意图缺少菜谱 ID")
        if self.action in {"CANCEL_RECIPE", "DELAY_RECIPE"} and not self.recipe_instance_id:
            raise ValueError("菜单变更必须引用明确实例")
        if self.action == "DELAY_RECIPE" and self.earliest_start_at is None:
            raise ValueError("时间偏好必须给出明确的开始时刻")
        if self.action == "CLARIFY" and not self.question:
            raise ValueError("澄清必须给出问题")
        return self


class LanguageRequest(FrozenModel):
    event_id: NonEmpty
    text: NonEmpty = Field(max_length=2000)
    expected_state_revision: NonNegativeInt
    base_plan_version: NonNegativeInt


class IntentContext(FrozenModel):
    session_id: str
    state_revision: int
    plan_version: int
    time_origin: AwareDatetime
    menu: tuple[dict[str, str], ...]
    catalog: tuple[dict[str, str], ...]
