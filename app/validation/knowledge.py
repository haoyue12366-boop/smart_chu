"""知识发布前的结构和工艺门；可调度性必须另由真实 Planner 证明。"""

from graphlib import CycleError, TopologicalSorter

from app.domain.canonical_recipe import Action, BatchPolicy, CanonicalRecipeModel
from app.domain.ids import OperationId
from app.domain.knowledge import KnowledgeValidationReport, ReleaseScope, knowledge_input_hash
from app.domain.processing_rules import ProcessingRule
from app.domain.resources import DeviceProfile, ResourceType
from app.domain.validation_contract import ValidationViolation
from app.validation.device_paths import legal_device_options
from app.validation.knowledge_materials import validate_material_paths
from app.validation.time_compatibility import validate_time_compatibility

VALIDATOR_VERSION = "knowledge-gate-v1"


def _recipe_violations(
    recipe: CanonicalRecipeModel,
    profiles: tuple[DeviceProfile, ...],
    scope: ReleaseScope,
) -> list[ValidationViolation]:
    issues: list[ValidationViolation] = []

    def fail(code: str, message: str, ref: str = "") -> None:
        issues.append(
            ValidationViolation(
                code=code,
                message=message,
                entity_refs=(recipe.recipe_id.root, ref or recipe.recipe_id.root),
                evidence_refs=recipe.provenance_refs,
            )
        )

    def evidence(refs: tuple[str, ...], ref: str) -> None:
        if not refs or not set(refs) <= set(scope.evidence_ids):
            fail("MISSING_EVIDENCE", "来源引用缺失或不在本发布证据索引中", ref)

    evidence(recipe.provenance_refs, "recipe")
    if not recipe.operations:
        fail("EMPTY_PATH", "菜谱没有可执行路径")
    if scope.release_kind != "development" and recipe.review_status != "APPROVED":
        fail("REVIEW_REQUIRED", "正式路径必须经过人工审核")
    graph: dict[OperationId, set[OperationId]] = {
        op.operation_id: set() for op in recipe.operations
    }
    for dep in recipe.dependencies:
        graph[dep.successor_id].add(dep.predecessor_id)
        evidence(dep.evidence_refs, dep.successor_id.root)
    ancestors: dict[OperationId, set[OperationId]] = {}
    try:
        for node_id in TopologicalSorter(graph).static_order():
            ancestors[node_id] = set(graph[node_id])
            for parent in graph[node_id]:
                ancestors[node_id].update(ancestors[parent])
    except CycleError:
        fail("PROCESS_CYCLE", "工艺依赖存在环")
    operations = {op.operation_id: op for op in recipe.operations}
    context = next((c for c in scope.recipe_contexts if c.recipe_id == recipe.recipe_id), None)
    completion = context.cooking_completion if context else None
    if completion is not None:
        evidence(completion.evidence_refs, "cooking_completion")
        for operation_id in completion.operation_ids:
            operation = operations.get(operation_id)
            if operation is None or not operation.required or operation.action == Action.FINISH:
                fail(
                    "COOKING_COMPLETION",
                    "出锅锚点不存在、非必需或错误引用装盘收尾",
                    operation_id.root,
                )
    for op in recipe.operations:
        ident = op.operation_id.root
        evidence(op.provenance_refs, ident)
        if op.required and op.duration.execution_sec is None:
            fail("MISSING_DURATION", "必需工序缺少选用时长", ident)
        if op.action == Action.UNKNOWN:
            fail("UNKNOWN_ACTION", "工序动作尚未明确", ident)
        if op.action in {Action.HEAT, Action.PREHEAT, Action.CHILL, Action.FREEZE} and not any(
            use.resource_type == ResourceType.DEVICE for use in op.resource_requirements
        ):
            fail("DEVICE_REQUIRED", "热处理或冷藏阶段缺少实际设备", ident)
        if op.duration.execution_sec is not None:
            evidence((op.duration.source_ref,) if op.duration.source_ref else (), ident)
        for use in op.resource_requirements:
            if use.resource_type == ResourceType.DEVICE and not legal_device_options(
                op, use, profiles, scope
            ):
                fail("DEVICE_PATH_INVALID", "设备映射、模式、参数、竞争规则或依据不完整", ident)
        policy = op.execution_policy
        if policy.batch_policy == BatchPolicy.FIXED_RECIPE and not policy.fixed_batch_id:
            fail("FIXED_BATCH_MISSING", "固定分批缺少批次身份", ident)
        if policy.holding_min_sec is not None and policy.holding_max_sec is not None:
            if policy.holding_min_sec > policy.holding_max_sec:
                fail("INVALID_HOLDING_WINDOW", "保温窗口上下界倒置", ident)
        for intervention in policy.interventions:
            target = operations[intervention.operation_id]
            if (
                not target.required
                or target.duration.execution_sec is None
                or not any(
                    r.resource_type == ResourceType.HUMAN for r in target.resource_requirements
                )
                or (
                    op.duration.execution_sec is not None
                    and intervention.offset_min_sec > op.duration.execution_sec
                )
            ):
                fail("INVALID_INTERVENTION", "强制介入缺少必需人工段、时长或有效窗口", ident)
    for spec in recipe.material_specs:
        evidence(spec.provenance_refs, spec.spec_id)
    for requirement in recipe.ingredient_requirements:
        evidence(requirement.provenance_refs, requirement.spec_id)
    issues.extend(validate_material_paths(recipe, ancestors))
    issues.extend(validate_time_compatibility(recipe, scope.time_grid_sec))
    baseline = next((r for r in scope.required_recipes if r.recipe_id == recipe.recipe_id), None)
    if baseline is not None:
        for required in baseline.operations:
            current = operations.get(required.operation_id)
            if required.required and (
                current is None
                or not current.required
                or current.action != required.action
                or current.execution_policy != required.execution_policy
            ):
                fail(
                    "MANDATORY_PROCESS_LOST",
                    "固定来源的强制工序、分批或介入发生丢失",
                    required.operation_id.root,
                )
    return issues


def validate_knowledge(
    recipes: tuple[CanonicalRecipeModel, ...],
    profiles: tuple[DeviceProfile, ...],
    rules: tuple[ProcessingRule, ...],
    release_scope: ReleaseScope,
) -> KnowledgeValidationReport:
    scope = release_scope
    issues: list[ValidationViolation] = []
    ids = [recipe.recipe_id for recipe in recipes]
    for label, values in (
        ("recipe", ids),
        ("profile", [p.profile_id for p in profiles]),
        ("device", [d.device_instance_id for d in scope.devices]),
        ("choice", [c.choice_id for c in scope.device_choices]),
        ("rule", [r.rule_id for r in rules]),
        ("evidence", list(scope.evidence_ids)),
    ):
        if not values or len(values) != len(set(values)):
            if values or label == "recipe":
                issues.append(
                    ValidationViolation(
                        code="DUPLICATE_OR_EMPTY_ID", message=f"{label} 身份重复或为空"
                    )
                )
    if scope.release_kind == "competition" and (
        len(ids) != 100
        or len(set(scope.expected_recipe_ids)) != 100
        or set(ids) != set(scope.expected_recipe_ids)
    ):
        issues.append(
            ValidationViolation(
                code="COMPETITION_ID_COVERAGE", message="正式发布必须匹配全部 100 个原始 ID"
            )
        )
    physical_policies: dict[tuple[str, str], str] = {}
    for device in scope.devices:
        if device.physical_resource_id and device.component_id and device.conflict_policy:
            key = device.competition_key
            if key in physical_policies and physical_policies[key] != device.conflict_policy:
                issues.append(
                    ValidationViolation(
                        code="PHYSICAL_POLICY_CONFLICT",
                        message="同一物理部件的能力别名竞争策略冲突",
                        entity_refs=(device.device_instance_id,),
                    )
                )
            physical_policies[key] = device.conflict_policy
    for rule in rules:
        if (
            rule.rule_version != scope.rule_version
            or not rule.evidence_refs
            or not set(rule.evidence_refs) <= set(scope.evidence_ids)
            or not set(rule.profile_ids) <= {p.profile_id for p in profiles}
            or (scope.release_kind != "development" and rule.review_status != "APPROVED")
        ):
            issues.append(
                ValidationViolation(
                    code="INVALID_RULE",
                    message="规则版本、审核或引用不完整",
                    entity_refs=(rule.rule_id,),
                )
            )
    usable = []
    for recipe in recipes:
        own = _recipe_violations(recipe, profiles, scope)
        issues.extend(own)
        if not own:
            usable.append(recipe.recipe_id)
    return KnowledgeValidationReport(
        input_hash=knowledge_input_hash(recipes, profiles, rules, scope),
        validator_version=VALIDATOR_VERSION,
        release_kind=scope.release_kind,
        valid=not issues,
        formal_release_eligible=not issues and scope.release_kind != "development",
        usable_recipe_ids=tuple(usable),
        violations=tuple(issues),
    )
