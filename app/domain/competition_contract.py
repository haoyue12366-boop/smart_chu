"""官方线协议结构与投影核对；不生成计划或发布业务成功响应。"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Annotated, Literal, Self

from pydantic import Field, StrictInt, TypeAdapter, model_validator

from app.domain.base import FrozenModel, NonEmpty, NonNegativeInt, PositiveInt
from app.domain.time import Interval, TimeOrigin

HHmm = Annotated[str, Field(strict=True, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")]
MinuteString = Annotated[str, Field(strict=True, pattern=r"^(?:0|[1-9]\d*)$")]
MinuteInterval = Annotated[str, Field(strict=True, pattern=r"^\d+-\d+$")]


class RecipeSelection(FrozenModel):
    id: StrictInt | NonEmpty
    name: NonEmpty


CompetitionRequest = list[RecipeSelection]
REQUEST_ADAPTER = TypeAdapter(CompetitionRequest)


def validate_request(
    payload: object, catalog: Mapping[str, str], *, id_mapping: Mapping[int, str] | None = None
) -> CompetitionRequest:
    request = REQUEST_ADAPTER.validate_python(payload)
    seen: set[str] = set()
    for selection in request:
        if isinstance(selection.id, int):
            rid = (id_mapping or {}).get(selection.id)
            if rid is None:
                raise ValueError("UNKNOWN_RECIPE：整数 ID 需要明确的官方映射")
        else:
            rid = selection.id
        if rid in seen:
            raise ValueError("INVALID_REQUEST：菜谱 ID 重复")
        if rid not in catalog:
            raise ValueError("UNKNOWN_RECIPE")
        if catalog[rid] != selection.name:
            raise ValueError("RECIPE_NAME_MISMATCH：名称必须与原库完全一致")
        seen.add(rid)
    return request


class Overview(FrozenModel):
    finishTime: HHmm
    timeSpent: MinuteString
    timeSave: MinuteString
    recipeCount: NonNegativeInt


class CookingTimelineItem(FrozenModel):
    name: NonEmpty
    product: NonEmpty
    startTime: HHmm
    endTime: HHmm
    timeSpent: MinuteString


class IngredientItem(FrozenModel):
    name: NonEmpty
    unit: NonEmpty


class IngredientGroup(FrozenModel):
    type: Literal["荤菜", "素菜", "调味品"]
    list: tuple[IngredientItem, ...]

    @model_validator(mode="after")
    def unique_ingredients(self) -> Self:
        if len({i.name for i in self.list}) != len(self.list):
            raise ValueError("同一分类食材应合并用量")
        return self


class DeviceParameter(FrozenModel):
    recipeName: NonEmpty
    temperature: StrictInt | Annotated[float, Field(strict=True, allow_inf_nan=False)] | NonEmpty
    time: PositiveInt


class DetailTimelineItem(FrozenModel):
    timeInterval: MinuteInterval
    title: NonEmpty
    type: Annotated[int, Field(strict=True, ge=1, le=6)]
    list: tuple[NonEmpty, ...]
    recipeNames: tuple[NonEmpty, ...] | None = None
    parameters: tuple[DeviceParameter, ...] | None = None

    @property
    def minute_bounds(self) -> tuple[int, int]:
        start, end = self.timeInterval.split("-")
        return int(start), int(end)

    @property
    def is_preheat(self) -> bool:
        return self.type == 5 and "预热" in self.title

    @model_validator(mode="after")
    def interval_and_parameters(self) -> Self:
        start, end = self.minute_bounds
        if end < start:
            raise ValueError("分钟区间倒置")
        if self.type in (3, 4, 5) and not self.recipeNames:
            raise ValueError("设备工作步骤必须返回 recipeNames")
        for parameter in self.parameters or ():
            if parameter.recipeName not in (self.recipeNames or ()):
                raise ValueError("参数菜谱不在当前 recipeNames 中")
            if parameter.time > end - start:
                raise ValueError("设备参数时长超出区间")
        if self.is_preheat:
            if not self.parameters or start == end:
                raise ValueError("预热必须包含真实非零时长和温度")
            if any(p.time != end - start for p in self.parameters):
                raise ValueError("预热参数时长与区间不一致")
        return self


class CookingParameters(FrozenModel):
    mode: NonEmpty
    temperature: NonEmpty
    time: NonEmpty


class CookingStep(FrozenModel):
    describe: NonEmpty
    cookingParameters: CookingParameters | None = None


class RecipeDetail(FrozenModel):
    name: NonEmpty
    majorIngredients: tuple[IngredientItem, ...]
    minorIngredients: tuple[IngredientItem, ...]
    cookingSteps: tuple[CookingStep, ...]


class CompetitionResponse(FrozenModel):
    overview: Overview
    cookingTimeline: tuple[CookingTimelineItem, ...]
    ingredientsSummary: tuple[IngredientGroup, ...]
    detailTimeline: tuple[DetailTimelineItem, ...]
    recipeDetail: tuple[RecipeDetail, ...]

    @model_validator(mode="after")
    def structure_consistency(self) -> Self:
        count = self.overview.recipeCount
        if len(self.cookingTimeline) != count or len(self.recipeDetail) != count:
            raise ValueError("响应菜谱数量不一致")
        names = Counter(item.name for item in self.cookingTimeline)
        if names != Counter(item.name for item in self.recipeDetail):
            raise ValueError("两种菜谱明细的名称及数量不一致")
        keys = [(item.type, item.minute_bounds[0]) for item in self.detailTimeline]
        if keys != sorted(keys):
            raise ValueError("详细时间线必须先按 type 再按开始分钟排序")
        total = int(self.overview.timeSpent)
        for item in self.detailTimeline:
            start, end = item.minute_bounds
            if end > total:
                raise ValueError("详细时间线超出总耗时")
            if any(name not in names for name in item.recipeNames or ()):
                raise ValueError("时间线引用未知菜谱")
            if item.type in (3, 4, 5) and not item.parameters:
                preheated = {
                    name
                    for previous in self.detailTimeline
                    if previous.is_preheat and previous.minute_bounds[1] <= start
                    for name in previous.recipeNames or ()
                }
                if item.type != 5 or not set(item.recipeNames or ()).issubset(preheated):
                    raise ValueError("设备步骤缺少参数且没有此前完成的预热")
        if len({group.type for group in self.ingredientsSummary}) != len(self.ingredientsSummary):
            raise ValueError("食材分类重复")
        return self


def validate_response_for_request(
    response: CompetitionResponse,
    request: CompetitionRequest,
    *,
    origin: TimeOrigin,
    recipe_intervals: tuple[Interval, ...],
    device_steps: tuple[tuple[int, int], ...],
) -> None:
    """用内部完整日期及设备阶段位置校验投影，不能从自然语言猜测设备事实。

    recipe_intervals 与 cookingTimeline 顺序一致；device_steps 为
    recipeDetail/cookingSteps 的零基位置。P5 Adapter 必须来自权威计划。
    """
    names = Counter(item.name for item in request)
    if response.overview.recipeCount != len(request):
        raise ValueError("菜谱数量不等于请求")
    if names != Counter(item.name for item in response.cookingTimeline):
        raise ValueError("响应遗漏或增加了请求菜谱")
    if len(recipe_intervals) != len(response.cookingTimeline):
        raise ValueError("缺少完整时间投影依据")
    if not recipe_intervals:
        raise ValueError("完整计划不能为空")
    last = max(i.end_sec for i in recipe_intervals)
    if int(response.overview.timeSpent) * 60 != last:
        raise ValueError("总耗时不能截断跨日时长")
    if origin.at(last).strftime("%H:%M") != response.overview.finishTime:
        raise ValueError("完成时刻投影不一致")
    for item, interval in zip(response.cookingTimeline, recipe_intervals, strict=True):
        if (
            origin.at(interval.start_sec).strftime("%H:%M"),
            origin.at(interval.end_sec).strftime("%H:%M"),
        ) != (item.startTime, item.endTime):
            raise ValueError("菜谱绝对时间投影不一致")
        if int(item.timeSpent) * 60 != interval.end_sec - interval.start_sec:
            raise ValueError("菜谱时长不一致")
    for recipe_index, step_index in device_steps:
        if recipe_index < 0 or step_index < 0:
            raise ValueError("设备步骤引用无效")
        try:
            parameters = (
                response.recipeDetail[recipe_index].cookingSteps[step_index].cookingParameters
            )
        except IndexError as exc:
            raise ValueError("设备步骤引用不存在") from exc
        if parameters is None:
            raise ValueError("设备工作步骤必须有 cookingParameters")
