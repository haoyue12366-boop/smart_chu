"""设备变化按物理竞争键及剩余需求判断，不依赖旧计划选中的能力别名。"""

from app.domain.candidates import stable_id
from app.domain.inventory import committed_fulfillments
from app.domain.knowledge import MenuKnowledgeView
from app.domain.resources import DeviceInstance
from app.domain.runtime_session import RuntimeSession


def _physical_state(session: RuntimeSession, key: tuple[str, str]) -> tuple[object, ...]:
    state = max(
        (
            item
            for item in session.runtime.device_states
            if (item.physical_resource_id, item.component_id) == key
        ),
        key=lambda item: item.observed_at,
        default=None,
    )
    details = session.runtime.details
    occupants = (
        tuple(
            sorted(
                (item.occupancy_id, item.execution_id.root, item.awaiting_confirmation)
                for item in details.occupancies
                if item.released_at is None
                and (item.resource.physical_resource_id, item.resource.component_id) == key
            )
        )
        if details
        else (state.occupancy_status, state.active_execution_id)
        if state
        else ()
    )
    return (
        state.availability_status if state else "AVAILABLE",
        state.expected_recovery_at if state else None,
        tuple(sorted((v.parameter, v.value) for v in state.configuration)) if state else (),
        state.thermal_state if state else None,
        occupants,
    )


def _affects_remaining(
    session: RuntimeSession, knowledge: MenuKnowledgeView, device: DeviceInstance
) -> bool:
    details = session.runtime.details
    cancelled = set(details.cancelled_instance_ids) if details else set()
    completed = {
        task.root
        for record in session.runtime.executions
        if record.status == "COMPLETED"
        for task in record.task_ids
    } | {task.root for item in committed_fulfillments(session.runtime) for task in item.task_ids}
    running = {
        task.root
        for record in session.runtime.executions
        if record.status == "RUNNING"
        for task in record.task_ids
    }
    recipes = {recipe.recipe_id: recipe for recipe in knowledge.recipes}
    choices = {choice.choice_id: choice.device_ids for choice in knowledge.device_choices}
    contexts = {context.recipe_id: context for context in knowledge.recipe_contexts}
    remaining = set(running)
    for instance in session.menu:
        if instance.recipe_instance_id.root in cancelled:
            continue
        recipe = recipes[instance.recipe_id]
        pending_operations = set()
        for operation in recipe.operations:
            task = stable_id("task", instance.recipe_instance_id.root, operation.operation_id.root)
            if task in completed:
                continue
            remaining.add(task)
            if task in running:
                continue  # 已开始执行只能使用冻结的实际设备。
            pending_operations.add(operation.operation_id)
            for use in operation.resource_requirements:
                if (
                    use.resource_type == "DEVICE"
                    and device.device_instance_id
                    in choices.get(use.resource_id, (use.resource_id,))
                    and device.physical_resource_id == use.physical_resource_id
                    and (use.resource_id in choices or device.component_id == use.component_id)
                    and device.conflict_policy == use.conflict_policy
                ):
                    return True
        context = contexts.get(instance.recipe_id)
        if context and any(
            set(reservation.members) & pending_operations
            and device.device_instance_id in reservation.resource_options
            for reservation in context.resource_reservations
        ):
            return True
    key = device.competition_key
    if any(
        remaining.intersection(task.root for task in binding.assignment.task_ids)
        and any(
            (use.physical_resource_id, use.component_id) == key
            for use in (
                *binding.carrier.resource_uses,
                *(phase.resource_use for phase in binding.carrier.resource_phases),
            )
        )
        for binding in session.bindings
    ):
        return True
    # 重启或取消后，实际运行占用仍可能存在，不以未来绑定代替执行事实。
    running_ids = {
        record.execution_id for record in session.runtime.executions if record.status == "RUNNING"
    }
    return bool(
        details
        and any(
            item.released_at is None
            and item.execution_id in running_ids
            and (item.resource.physical_resource_id, item.resource.component_id) == key
            for item in details.occupancies
        )
    )


def device_requires_replan(
    before: RuntimeSession,
    after: RuntimeSession,
    device_id: str,
    knowledge: MenuKnowledgeView,
) -> bool:
    device = next(item for item in knowledge.devices if item.device_instance_id == device_id)
    key = device.competition_key
    if _physical_state(before, key) == _physical_state(after, key):
        return False
    # 用规范设备逐个检查同一物理资源，事件别名不必出现在菜谱候选中。
    return any(
        item.competition_key == key and _affects_remaining(before, knowledge, item)
        for item in knowledge.devices
    )
