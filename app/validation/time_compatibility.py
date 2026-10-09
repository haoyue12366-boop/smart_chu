"""检查已选时间是否可表达；不修改加热时长或静默取整。"""

from app.domain.canonical_recipe import CanonicalRecipeModel
from app.domain.time import align_down, align_up
from app.domain.validation_contract import ValidationViolation


def validate_time_compatibility(
    recipe: CanonicalRecipeModel, grid_sec: int
) -> tuple[ValidationViolation, ...]:
    if grid_sec <= 0:
        raise ValueError("时间网格必须为正")
    violations = []

    def fail(ref: str, message: str) -> None:
        violations.append(
            ValidationViolation(
                code="TIME_GRID_INCOMPATIBLE",
                message=message,
                entity_refs=(recipe.recipe_id.root, ref),
                evidence_refs=recipe.provenance_refs,
            )
        )

    for op in recipe.operations:
        seconds = op.duration.execution_sec
        if seconds is not None and seconds % grid_sec:
            fail(op.operation_id.root, "选用时长不能在当前网格表达；需明确的离线时长方案")
        for intervention in op.execution_policy.interventions:
            if align_up(intervention.offset_min_sec, grid_sec) > align_down(
                intervention.offset_max_sec, grid_sec
            ):
                fail(op.operation_id.root, "人工介入窗口在当前网格为空")
        policy = op.execution_policy
        if policy.holding_min_sec is not None and policy.holding_max_sec is not None:
            if align_up(policy.holding_min_sec, grid_sec) > align_down(
                policy.holding_max_sec, grid_sec
            ):
                fail(op.operation_id.root, "保温时间窗口在当前网格为空")
    for edge in recipe.dependencies:
        if edge.max_lag_sec is not None and align_up(edge.min_lag_sec, grid_sec) > align_down(
            edge.max_lag_sec, grid_sec
        ):
            fail(edge.successor_id.root, "工序最小和最大间隔在当前网格冲突")
    return tuple(violations)
