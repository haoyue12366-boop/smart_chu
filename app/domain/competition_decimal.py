"""P5 项目扩展协议：累计响应与一位小数分钟，保留旧整数契约。"""

from collections import Counter
from decimal import Decimal
from typing import Annotated, Self

from pydantic import Field, field_validator, model_validator

from app.domain.base import FrozenModel, NonEmpty, NonNegativeInt
from app.domain.competition_contract import HHmm, IngredientGroup, IngredientItem
from app.domain.planning_timing import PlanningOverhead

DecimalMinute = Annotated[str, Field(strict=True, pattern=r"^(?:0|[1-9]\d*)\.\d$")]
DecimalInterval = Annotated[str, Field(strict=True, pattern=r"^\d+\.\d-\d+\.\d$")]


class DecimalOverview(FrozenModel):
    finishTime: HHmm
    timeSpent: DecimalMinute
    timeSave: DecimalMinute
    recipeCount: NonNegativeInt
    planningOverhead: PlanningOverhead | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


class DecimalCookingTimeline(FrozenModel):
    name: NonEmpty
    product: NonEmpty
    startTime: HHmm
    endTime: HHmm
    timeSpent: DecimalMinute


class DecimalDeviceParameter(FrozenModel):
    recipeName: NonEmpty
    temperature: int | float | NonEmpty
    time: Annotated[float, Field(ge=0, allow_inf_nan=False)]

    @field_validator("time")
    @classmethod
    def precision(cls, value: float) -> float:
        if Decimal(str(value)) * 10 != (Decimal(str(value)) * 10).to_integral_value():
            raise ValueError("分钟参数只能有一位小数")
        return value


class DecimalDetailTimeline(FrozenModel):
    timeInterval: DecimalInterval
    title: NonEmpty
    type: Annotated[int, Field(strict=True, ge=1, le=6)]
    list: tuple[NonEmpty, ...]
    recipeNames: tuple[NonEmpty, ...] | None = None
    parameters: tuple[DecimalDeviceParameter, ...] | None = None

    @property
    def bounds(self) -> tuple[Decimal, Decimal]:
        start, end = self.timeInterval.split("-")
        return Decimal(start), Decimal(end)

    @model_validator(mode="after")
    def structure(self) -> Self:
        start, end = self.bounds
        if start > end:
            raise ValueError("时间区间倒置")
        if self.type in {3, 4, 5} and not self.recipeNames:
            raise ValueError("设备工作缺少成员名称")
        if any(p.recipeName not in (self.recipeNames or ()) for p in self.parameters or ()):
            raise ValueError("设备参数引用不属于当前步骤")
        return self


class DecimalCookingParameters(FrozenModel):
    mode: NonEmpty
    temperature: NonEmpty
    time: DecimalMinute


class DecimalCookingStep(FrozenModel):
    describe: NonEmpty
    cookingParameters: DecimalCookingParameters | None = None


class DecimalRecipeDetail(FrozenModel):
    name: NonEmpty
    majorIngredients: tuple[IngredientItem, ...]
    minorIngredients: tuple[IngredientItem, ...]
    cookingSteps: tuple[DecimalCookingStep, ...]


class DecimalCompetitionResponse(FrozenModel):
    overview: DecimalOverview
    cookingTimeline: tuple[DecimalCookingTimeline, ...]
    ingredientsSummary: tuple[IngredientGroup, ...]
    detailTimeline: tuple[DecimalDetailTimeline, ...]
    recipeDetail: tuple[DecimalRecipeDetail, ...]

    @model_validator(mode="after")
    def structure(self) -> Self:
        count = self.overview.recipeCount
        if len(self.cookingTimeline) != count or len(self.recipeDetail) != count:
            raise ValueError("累计菜单数量不一致")
        names = Counter(item.name for item in self.cookingTimeline)
        if names != Counter(item.name for item in self.recipeDetail):
            raise ValueError("菜谱明细身份数量不一致")
        keys = [(item.type, item.bounds[0]) for item in self.detailTimeline]
        if keys != sorted(keys):
            raise ValueError("详细时间线应按类型和开始时间排序")
        if any(item.bounds[1] > Decimal(self.overview.timeSpent) for item in self.detailTimeline):
            raise ValueError("详细时间线超过会话总跨度")
        if any(
            name not in names for item in self.detailTimeline for name in item.recipeNames or ()
        ):
            raise ValueError("详细时间线引用未知菜品")
        return self
