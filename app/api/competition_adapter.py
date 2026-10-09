"""比赛结果只从请求绑定的持久发布与其问题快照生成。"""

from collections import defaultdict
from zoneinfo import ZoneInfo

from app.api.competition_ingredients import ingredient_summary, recipe_ingredients
from app.api.competition_steps import (
    ProjectedTask,
    device_description,
    projected_tasks,
    step_description,
    step_kind,
)
from app.api.minute_projection import minute_text, projected_interval
from app.domain.competition_decimal import (
    DecimalCompetitionResponse,
    DecimalCookingParameters,
    DecimalCookingStep,
    DecimalCookingTimeline,
    DecimalDetailTimeline,
    DecimalDeviceParameter,
    DecimalOverview,
    DecimalRecipeDetail,
)
from app.domain.errors import ServiceError
from app.domain.knowledge import MenuKnowledgeView
from app.domain.schedule import PublishedPlan
from app.domain.scheduling_problem import SchedulingProblem


class CompetitionAdapter:
    def __init__(self, timezone: str = "Asia/Shanghai") -> None:
        self.timezone = ZoneInfo(timezone)

    def to_response(
        self, plan: PublishedPlan, problem: SchedulingProblem, knowledge: MenuKnowledgeView
    ) -> DecimalCompetitionResponse:
        if plan.validated.candidate.problem_hash != problem.problem_hash:
            raise ServiceError("STATE_INCOMPLETE", "发布计划与问题快照身份不一致")
        steps = projected_tasks(plan, problem)
        recipes = {r.recipe_id: r for r in knowledge.recipes}
        by_recipe = {
            r.recipe_instance_id: tuple(
                s for s in steps if s.task.recipe_instance_id == r.recipe_instance_id
            )
            for r in problem.recipe_instances
        }
        timeline, details = [], []
        completions = {
            item.recipe_instance_id: item.completion_sec
            for item in plan.validated.candidate.recipe_completions
        }
        for instance in problem.recipe_instances:
            members = sorted(
                by_recipe[instance.recipe_instance_id],
                key=lambda s: (s.interval.start_sec, s.interval.end_sec, s.task.operation_id.root),
            )
            if not members:
                raise ServiceError("STATE_INCOMPLETE", "菜品缺少可追溯的工序")
            start = min(s.interval.start_sec for s in members)
            end = completions.get(
                instance.recipe_instance_id, max(s.interval.end_sec for s in members)
            )
            primary = max(
                members,
                key=lambda s: (
                    (s.interval.end_sec - s.interval.start_sec)
                    if s.task.operation.action == "HEAT"
                    else -1
                ),
            )
            device, _, _ = device_description(primary, problem)
            timeline.append(
                DecimalCookingTimeline(
                    name=instance.name,
                    product=device,
                    startTime=plan.time_origin.at(start)
                    .astimezone(self.timezone)
                    .strftime("%H:%M"),
                    endTime=plan.time_origin.at(end).astimezone(self.timezone).strftime("%H:%M"),
                    timeSpent=minute_text(end - start),
                )
            )
            major, minor = recipe_ingredients(recipes[instance.recipe_id])
            cooking_steps = [
                DecimalCookingStep(
                    describe=(
                        f"开工前准备（按用户策略已备好）：{item.description}；"
                        f"原准备时长{minute_text(item.original_duration_sec)}分钟"
                    ),
                    cookingParameters=None,
                )
                for item in problem.advance_preparations
                if item.recipe_instance_id == instance.recipe_instance_id
            ]
            for step in members:
                _, mode, temperature = device_description(step, problem)
                parameters = (
                    DecimalCookingParameters(
                        mode=mode,
                        temperature=str(temperature),
                        time=minute_text(step.interval.end_sec - step.interval.start_sec),
                    )
                    if any(u.resource_type == "DEVICE" for u in step.resources)
                    else None
                )
                cooking_steps.append(
                    DecimalCookingStep(
                        describe=step_description(step, problem), cookingParameters=parameters
                    )
                )
            details.append(
                DecimalRecipeDetail(
                    name=instance.name,
                    majorIngredients=major,
                    minorIngredients=minor,
                    cookingSteps=tuple(cooking_steps),
                )
            )
        metrics = plan.validated.candidate.metrics
        reference_metrics = (
            plan.serial_reference.candidate.metrics if plan.serial_reference else None
        )
        if metrics is None or reference_metrics is None:
            raise ServiceError("STATE_INCOMPLETE", "发布计划缺少已验证的串行参考")
        saved = reference_metrics.makespan_sec - metrics.makespan_sec
        if saved < 0:
            raise ServiceError("STATE_INCOMPLETE", "串行参考与当前会话统计口径不一致")
        response = DecimalCompetitionResponse(
            overview=DecimalOverview(
                finishTime=plan.time_origin.at(metrics.makespan_sec)
                .astimezone(self.timezone)
                .strftime("%H:%M"),
                timeSpent=minute_text(metrics.makespan_sec),
                timeSave=minute_text(saved),
                recipeCount=len(problem.recipe_instances),
                planningOverhead=plan.planning_overhead,
            ),
            cookingTimeline=tuple(timeline),
            recipeDetail=tuple(details),
            ingredientsSummary=ingredient_summary(
                tuple(recipes[r.recipe_id] for r in problem.recipe_instances)
            ),
            detailTimeline=self._timeline(steps, problem),
        )
        return response

    def _timeline(
        self, steps: tuple[ProjectedTask, ...], problem: SchedulingProblem
    ) -> tuple[DecimalDetailTimeline, ...]:
        names = {r.recipe_instance_id: r.name for r in problem.recipe_instances}
        groups: dict[tuple[str, int, int, int], list[ProjectedTask]] = defaultdict(list)
        for step in steps:
            kind, _ = step_kind(step, problem)
            groups[(step.owner, step.interval.start_sec, step.interval.end_sec, kind)].append(step)
        result = []
        for (_, start, end, kind), members in groups.items():
            parameters = []
            for step in members:
                if kind in {3, 4, 5}:
                    _, _, temperature = device_description(step, problem)
                    parameter = DecimalDeviceParameter(
                        recipeName=names[step.task.recipe_instance_id],
                        temperature=temperature,
                        time=float(minute_text(end - start)),
                    )
                    if parameter not in parameters:
                        parameters.append(parameter)
            result.append(
                DecimalDetailTimeline(
                    timeInterval=projected_interval(start, end),
                    type=kind,
                    title=step_kind(members[0], problem)[1],
                    list=tuple(dict.fromkeys(step_description(s, problem) for s in members)),
                    recipeNames=tuple(
                        dict.fromkeys(names[s.task.recipe_instance_id] for s in members)
                    )
                    if kind in {3, 4, 5}
                    else None,
                    parameters=tuple(parameters) or None,
                )
            )
        return tuple(sorted(result, key=lambda item: (item.type, item.bounds[0], item.bounds[1])))
