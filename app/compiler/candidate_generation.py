"""逐任务保留设备合法备选；这里不选择最终设备或生成跨菜共享。"""

from itertools import product

from app.compiler.instantiate import failure
from app.domain.base import content_hash
from app.domain.candidates import InstantiationResult, stable_id
from app.domain.knowledge import MenuKnowledgeView
from app.domain.reports import CompilationFailure
from app.domain.resources import ResourceUse
from app.domain.scheduling_problem import CandidateCarrier


def standalone_candidates(
    instantiated: InstantiationResult, knowledge: MenuKnowledgeView
) -> tuple[CandidateCarrier, ...] | CompilationFailure:
    devices = {d.device_instance_id: d for d in knowledge.devices}
    choices = {c.choice_id: c.device_ids for c in knowledge.device_choices}
    fixed = set(instantiated.completed_task_ids) | set(instantiated.running_task_ids)
    result = []
    for task in instantiated.tasks:
        if task.task_id in fixed:
            continue
        operation = task.operation
        seconds = operation.duration.execution_sec
        if seconds is None or operation.action == "UNKNOWN":
            return failure("必需操作缺少时长或已知动作：" + task.operation_id.root)
        options: list[tuple[ResourceUse, ...]] = []
        for use in operation.resource_requirements:
            if use.resource_type == "HUMAN":
                options.append((use,))
                continue
            resolved = []
            for device_id in choices.get(use.resource_id, (use.resource_id,)):
                device = devices.get(device_id)
                if device is None or not device.physical_resource_id or not device.component_id:
                    continue
                if device.physical_resource_id != use.physical_resource_id:
                    continue
                if use.resource_id not in choices and device.component_id != use.component_id:
                    continue
                if use.effective_layer_indices and (
                    device.capacity <= 1
                    or any(layer > device.capacity for layer in use.effective_layer_indices)
                ):
                    continue
                if device.capacity > 1 and (
                    use.units > device.capacity or use.units > 1 and not use.occupied_layer_indices
                ):
                    continue
                if device.conflict_policy != use.conflict_policy and not (
                    device.capacity > 1
                    and device.conflict_policy == "STATE_COMPATIBLE"
                    and use.conflict_policy in {"UNARY", "BATCH_EXCLUSIVE"}
                ):
                    continue
                resolved.append(
                    ResourceUse.model_validate(
                        {
                            **use.model_dump(),
                            "resource_id": device_id,
                            "physical_resource_id": device.physical_resource_id,
                            "component_id": device.component_id,
                        }
                    )
                )
            if not resolved:
                return failure("必需设备没有有效物理映射：" + task.operation_id.root)
            options.append(tuple(resolved))
        for selected in product(*options):
            candidate = CandidateCarrier(
                carrier_id="pending",
                kind="STANDALONE",
                covers=(task.task_id,),
                duration_sec=seconds,
                resource_uses=tuple(selected),
                material_inputs=operation.material_inputs,
                material_outputs=operation.material_outputs,
                mandatory_recipe_batch=operation.execution_policy.batch_policy == "FIXED_RECIPE",
                provenance_refs=operation.provenance_refs,
            )
            result.append(
                CandidateCarrier.model_validate(
                    {
                        **candidate.model_dump(),
                        "carrier_id": stable_id(
                            "carrier", knowledge.release.rule_version, content_hash(candidate)
                        ),
                    }
                )
            )
    return tuple(result)
