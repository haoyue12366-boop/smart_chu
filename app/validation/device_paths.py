"""逐阶段核对真实设备映射与各自能力，不根据设备名称增加资源。"""

from app.domain.canonical_recipe import Action, OperationTemplate
from app.domain.knowledge import ReleaseScope
from app.domain.resources import DeviceInstance, DeviceProfile, ParameterConstraint, ResourceUse


def _constraints_match(
    values: dict[str, int | str], constraints: tuple[ParameterConstraint, ...]
) -> bool:
    if len({c.parameter for c in constraints}) != len(constraints):
        return False
    for constraint in constraints:
        value = values.get(constraint.parameter)
        if value is None:
            return False
        if constraint.allowed_values and value not in constraint.allowed_values:
            return False
        if constraint.minimum is not None or constraint.maximum is not None:
            if type(value) is not int:
                return False
            if constraint.minimum is not None and value < constraint.minimum:
                return False
            if constraint.maximum is not None and value > constraint.maximum:
                return False
    return True


def legal_device_options(
    operation: OperationTemplate,
    use: ResourceUse,
    profiles: tuple[DeviceProfile, ...],
    scope: ReleaseScope,
) -> tuple[str, ...]:
    known_evidence = set(scope.evidence_ids)
    if (
        not use.physical_resource_id
        or not use.component_id
        or not use.conflict_policy
        or use.rule_version != scope.rule_version
        or not use.evidence_refs
        or not set(use.evidence_refs) <= known_evidence
    ):
        return ()
    values = {item.parameter: item.value for item in use.configuration}
    if len(values) != len(use.configuration):
        return ()
    if operation.duration.execution_sec is not None:
        if "duration_sec" in values and values["duration_sec"] != operation.duration.execution_sec:
            return ()
        values["duration_sec"] = operation.duration.execution_sec
    choices = {c.choice_id: c.device_ids for c in scope.device_choices}
    allowed_ids = choices.get(use.resource_id, (use.resource_id,))
    result = []
    for device in scope.devices:
        if device.device_instance_id not in allowed_ids or not _mapping_matches(device, use, scope):
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
        candidates = [
            p
            for p in profiles
            if p.profile_id in device.capability_refs
            and (not use.profile_options or p.profile_id in use.profile_options)
        ]
        for profile in candidates:
            if (
                profile.rule_version != scope.rule_version
                or not profile.provenance_refs
                or not set(profile.provenance_refs) <= known_evidence
                or values.get("mode", profile.mode) != profile.mode
            ):
                continue
            if scope.release_kind != "development" and profile.review_status != "APPROVED":
                continue
            constraints = tuple(
                c
                for c in profile.constraints
                if c.parameter != "duration_sec"
                or operation.action in {Action.HEAT, Action.PREHEAT, Action.CHILL, Action.FREEZE}
            )
            allowed_parameters = {c.parameter for c in profile.constraints} | {
                "mode",
                "duration_sec",
            }
            if not set(values) <= allowed_parameters:
                continue
            if _constraints_match(values, constraints) and _constraints_match(
                values, device.profile_constraints
            ):
                result.append(device.device_instance_id)
                break
    return tuple(result)


def _mapping_matches(device: DeviceInstance, use: ResourceUse, scope: ReleaseScope) -> bool:
    # 一个明确的资源选择可以选择两个灶眼；其物理灶具必须一致。
    is_choice = any(c.choice_id == use.resource_id for c in scope.device_choices)
    return (
        device.physical_resource_id == use.physical_resource_id
        and (is_choice or device.component_id == use.component_id)
        and device.component_id is not None
        and (
            device.conflict_policy == use.conflict_policy
            or (
                device.capacity > 1
                and device.conflict_policy == "STATE_COMPATIBLE"
                and use.conflict_policy in {"UNARY", "BATCH_EXCLUSIVE"}
            )
        )
        and device.rule_version == scope.rule_version
        and bool(device.evidence_refs)
        and set(device.evidence_refs) <= set(scope.evidence_ids)
        and (
            scope.release_kind == "development"
            or (device.review_status == "APPROVED" and use.review_status == "APPROVED")
        )
    )
