"""形成来源完整的共享实施蓝图；不把蓝图当作候选计划或收益报告。"""

from collections import Counter
from typing import TypedDict

from app.domain.canonical_recipe import CanonicalRecipeModel, OperationTemplate
from app.domain.compatibility import GroupRuleSpec
from app.domain.knowledge_release import ReviewedRelease


class SourceOperation(TypedDict):
    recipe_id: str
    recipe_name: str
    operation_id: str
    action: str
    duration_sec: int
    source: dict[str, object]


def _ports(recipe: CanonicalRecipeModel, op: OperationTemplate) -> list[dict[str, object]]:
    result = []
    for output in op.material_outputs:
        consumers = [
            {
                "operation_id": other.operation_id.root,
                "requirement": requirement.model_dump(mode="json"),
            }
            for other in recipe.operations
            for requirement in other.material_inputs
            if requirement.spec_id == output.spec_id
        ]
        result.append(
            {
                "port_id": f"{recipe.recipe_id.root}/{op.operation_id.root}/{output.spec_id}",
                "recipe_id": recipe.recipe_id.root,
                "producer_operation_id": op.operation_id.root,
                **output.model_dump(mode="json"),
                "consumers": consumers,
            }
        )
    return result


def build_preflight(source: ReviewedRelease) -> dict[str, object]:
    recipes = {r.recipe_id: r for r in source.recipes}
    contexts = {c.recipe_id: c for c in source.scope.recipe_contexts}
    groups: list[dict[str, object]] = []
    for rule in source.rules:
        spec = GroupRuleSpec.model_validate_json(rule.group_compatibility_predicate)
        operations: list[SourceOperation] = []
        outputs = []
        dependencies: list[dict[str, object]] = []
        replaced = []
        for binding in spec.bindings:
            recipe = recipes[binding.recipe_id]
            target = next(o for o in recipe.operations if o.operation_id == binding.operation_id)
            selected = {target.operation_id}
            if rule.kind == "STRICT_TOGETHER":
                context = contexts[recipe.recipe_id]
                reservations = [
                    r for r in context.resource_reservations if target.operation_id in r.members
                ]
                if len(reservations) != 1:
                    raise ValueError("热工序必须对应唯一可追溯外层预约")
                selected = set(reservations[0].members)
            for op in recipe.operations:
                if op.operation_id not in selected:
                    continue
                if op.duration.execution_sec is None:
                    raise ValueError("准备范围存在未知时长")
                replaced.append(f"{recipe.recipe_id.root}/{op.operation_id.root}")
                operations.append(
                    {
                        "recipe_id": recipe.recipe_id.root,
                        "recipe_name": recipe.name,
                        "operation_id": op.operation_id.root,
                        "action": op.action.value,
                        "duration_sec": op.duration.execution_sec,
                        "source": op.model_dump(mode="json"),
                    }
                )
            outputs.extend(_ports(recipe, target))
            dependencies.extend(
                {"recipe_id": recipe.recipe_id.root, **d.model_dump(mode="json")}
                for d in recipe.dependencies
                if d.predecessor_id in selected or d.successor_id in selected
            )
        group: dict[str, object] = {
            "rule_id": rule.rule_id,
            "kind": rule.kind.value,
            "duration_sec": spec.duration_sec,
            "source_operations": operations,
            "replaced_operation_refs": replaced,
            "incident_dependencies": dependencies,
            "output_ports": outputs,
            "runtime_enabled": False,
            "standalone_preserved": True,
            "mass_yield": None,
            "actual_inventory_created": False,
            "allocation_contract": "按各菜源端口完整份额分配；不跨端口混算克重，不合成已完成库存",
        }
        if rule.kind == "STRICT_TOGETHER":
            seconds = {
                action: [op["duration_sec"] for op in operations if op["action"] == action]
                for action in ("LOAD", "PREPARE", "PREHEAT", "HEAT", "UNLOAD")
            }
            if any(op["action"] not in seconds for op in operations):
                raise ValueError("首批热蓝图含尚未定义的介入或转换阶段")
            if (
                len(set(seconds["PREHEAT"])) != 1
                or len(set(seconds["HEAT"])) != 1
                or seconds["HEAT"][0] != spec.duration_sec
            ):
                raise ValueError("共同预热或等长加热条件不满足")
            phases = {
                action: (values[0] if action in {"PREHEAT", "HEAT"} else sum(values))
                for action, values in seconds.items()
            }
            group["phase_seconds"] = phases
            group["reservation_sec"] = sum(phases.values())
            group["model_scope"] = "DELEGATED_SIMULATION_ONLY; residual heat not measured"
            group["requires_full_subgraph_mapping"] = True
        groups.append(group)
    all_operations = [o for r in source.recipes for o in r.operations]
    return {
        "kind": "P3_IMPLEMENTATION_PREFLIGHT_NOT_A_PLAN",
        "release_id": source.release_id,
        "knowledge_version": source.knowledge_version,
        "rule_version": source.scope.rule_version,
        "source_content_hash": source.content_hash,
        "planning_result": "NOT_RUN",
        "shared_runtime_enabled": False,
        "groups": groups,
        "audit": {
            "recipe_count": len(source.recipes),
            "operation_count": len(all_operations),
            "missing_execution_duration": sum(
                o.duration.execution_sec is None for o in all_operations
            ),
            "recipe_review_states": dict(Counter(r.review_status.value for r in source.recipes)),
            "qualitative_output_count": sum(
                m.quantity_kind == "QUALITATIVE" for o in all_operations for m in o.material_outputs
            ),
            "recipe_id_distinct_count": len({r.recipe_id for r in source.recipes}),
        },
    }
