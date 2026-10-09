"""五字段结构之外独立核对真实时间、菜谱身份、步骤覆盖及设备参数。"""

from collections import Counter, defaultdict
from zoneinfo import ZoneInfo

from app.domain.advance_preparation import AdvancePreparation
from app.domain.competition_decimal import DecimalCompetitionResponse, DecimalCookingStep
from app.domain.knowledge import MenuKnowledgeView
from app.domain.minute_projection import minute_text, projected_interval
from app.domain.schedule import PublishedPlan
from app.domain.scheduling_problem import SchedulingProblem
from app.validation.competition_facts import ProjectionFact, kind, parameters, projection_facts
from app.validation.competition_ingredients import (
    check_ingredient_summary,
    check_recipe_ingredients,
)


def validate_competition_projection(
    response: DecimalCompetitionResponse,
    plan: PublishedPlan,
    problem: SchedulingProblem,
    knowledge: MenuKnowledgeView,
    *,
    timezone: str,
) -> None:
    if (
        plan.validated.candidate.problem_hash != problem.problem_hash
        or plan.snapshot_id != knowledge.release.snapshot_id
        or plan.knowledge_version != knowledge.release.knowledge_version
        or plan.session_id != problem.runtime.session_id
    ):
        raise ValueError("响应上下文身份不一致")
    metrics = plan.validated.candidate.metrics
    reference = plan.serial_reference.candidate.metrics if plan.serial_reference else None
    if metrics is None or reference is None:
        raise ValueError("缺少经过验证的指标或串行参考")
    zone = ZoneInfo(timezone)

    def clock(sec: int) -> str:
        return plan.time_origin.at(sec).astimezone(zone).strftime("%H:%M")

    if (
        response.overview.recipeCount != len(problem.recipe_instances)
        or response.overview.timeSpent != minute_text(metrics.makespan_sec)
        or response.overview.timeSave != minute_text(reference.makespan_sec - metrics.makespan_sec)
        or response.overview.finishTime != clock(metrics.makespan_sec)
    ):
        raise ValueError("总览不属于已发布计划")
    facts = projection_facts(plan, problem)
    recipes = {r.recipe_id: r for r in knowledge.recipes}
    completions = {
        c.recipe_instance_id: c.completion_sec for c in plan.validated.candidate.recipe_completions
    }
    for instance, timeline, detail in zip(
        problem.recipe_instances, response.cookingTimeline, response.recipeDetail, strict=True
    ):
        members = sorted(
            (f for f in facts if f.task.recipe_instance_id == instance.recipe_instance_id),
            key=lambda f: (f.start, f.end, f.task.operation_id.root),
        )
        if not members or timeline.name != instance.name or detail.name != instance.name:
            raise ValueError("菜谱行的身份、名称或覆盖不一致")
        low = min(f.start for f in members)
        high = completions.get(instance.recipe_instance_id, max(f.end for f in members))
        if (
            timeline.startTime != clock(low)
            or timeline.endTime != clock(high)
            or timeline.timeSpent != minute_text(high - low)
        ):
            raise ValueError("菜谱时刻或时长不一致")
        primary = max(
            members, key=lambda f: f.end - f.start if f.task.operation.action == "HEAT" else -1
        )
        if timeline.product != parameters(primary, problem)[0]:
            raise ValueError("菜谱主要设备不一致")
        _check_recipe_steps(
            detail.cookingSteps,
            members,
            problem,
            tuple(
                item
                for item in problem.advance_preparations
                if item.recipe_instance_id == instance.recipe_instance_id
            ),
        )
        check_recipe_ingredients(
            (*detail.majorIngredients, *detail.minorIngredients), recipes[instance.recipe_id]
        )
    check_ingredient_summary(
        response.ingredientsSummary, tuple(recipes[i.recipe_id] for i in problem.recipe_instances)
    )
    _check_timeline(response, facts, problem)


def _check_recipe_steps(
    steps: tuple[DecimalCookingStep, ...],
    facts: list[ProjectionFact],
    problem: SchedulingProblem,
    preparations: tuple[AdvancePreparation, ...] = (),
) -> None:
    if len(steps) != len(facts) + len(preparations):
        raise ValueError("菜谱缺少必需步骤")
    for step, preparation in zip(steps[: len(preparations)], preparations, strict=True):
        expected = (
            f"开工前准备（按用户策略已备好）：{preparation.description}；"
            f"原准备时长{minute_text(preparation.original_duration_sec)}分钟"
        )
        if step.describe != expected or step.cookingParameters is not None:
            raise ValueError("开工前准备声明缺失或被冒充为设备加工")
    for step, fact in zip(steps[len(preparations) :], facts, strict=True):
        description = fact.task.operation.description or fact.task.operation.action.value
        if _operation_text(step.describe) != description:
            raise ValueError("步骤描述不对应当前菜谱工序")
        device, mode, temperature = parameters(fact, problem)
        expected_device = any(r.resource_type == "DEVICE" for r in fact.resources)
        if expected_device:
            p = step.cookingParameters
            if p is None or (p.mode, p.temperature, p.time) != (
                mode,
                str(temperature),
                minute_text(fact.end - fact.start),
            ):
                raise ValueError("工序设备参数不来自已审核工艺与真实区间")
        elif step.cookingParameters is not None:
            raise ValueError("无设备工序不能增加设备参数")
        if fact.task.operation.action in {"HEAT", "PREHEAT"} and not fact.inventory:
            if any(
                text not in step.describe
                for text in (
                    device,
                    mode,
                    f"温度/火力{temperature}",
                    f"时间{minute_text(fact.end - fact.start)}分钟",
                )
            ):
                raise ValueError("步骤文字与结构化设备事实不一致")


ProjectionKey = tuple[str, int, frozenset[str], frozenset[str], frozenset[tuple[str, str, str]]]


def _operation_text(text: str) -> str:
    if text.startswith("将食材放入") and "；" in text:
        return text.split("；", 1)[1]
    for prefix in ("准备食材备用：", "出锅/装盘：", "将食材腌制：", "已有合格备料满足："):
        if text.startswith(prefix):
            return text[len(prefix) :]
    return text


def _check_timeline(
    response: DecimalCompetitionResponse,
    facts: tuple[ProjectionFact, ...],
    problem: SchedulingProblem,
) -> None:
    groups: dict[tuple[str, int, int, int], list[ProjectionFact]] = defaultdict(list)
    names = {i.recipe_instance_id: i.name for i in problem.recipe_instances}
    for fact in facts:
        groups[(fact.owner, fact.start, fact.end, kind(fact, problem))].append(fact)
    expected: Counter[ProjectionKey] = Counter()
    for (_, start, end, category), members in groups.items():
        descriptions = frozenset(
            f.task.operation.description or f.task.operation.action.value for f in members
        )
        member_names = (
            frozenset(names[f.task.recipe_instance_id] for f in members)
            if category in {3, 4, 5}
            else frozenset()
        )
        expected_parameters = (
            frozenset(
                (
                    names[f.task.recipe_instance_id],
                    str(parameters(f, problem)[2]),
                    minute_text(end - start),
                )
                for f in members
            )
            if category in {3, 4, 5}
            else frozenset()
        )
        expected[
            (
                projected_interval(start, end),
                category,
                descriptions,
                member_names,
                expected_parameters,
            )
        ] += 1
    actual: Counter[ProjectionKey] = Counter()
    for item in response.detailTimeline:
        descriptions = frozenset(_operation_text(text) for text in item.list)
        actual[
            (
                item.timeInterval,
                item.type,
                descriptions,
                frozenset(item.recipeNames or ()),
                frozenset(
                    (p.recipeName, str(p.temperature), format(p.time, ".1f"))
                    for p in item.parameters or ()
                ),
            )
        ] += 1
    if actual != expected:
        raise ValueError("详细时间线的区间、成员覆盖、类别或参数与真实工序不一致")
