"""菜单事件保留菜谱实例身份及已开始事实。"""

from app.domain.advance_preparation import active_preparations
from app.domain.candidates import stable_id
from app.domain.events import MenuPayload, RuntimeEvent
from app.domain.ids import RecipeInstanceId
from app.domain.knowledge import MenuKnowledgeView
from app.domain.runtime_facts import LotFact, RationalAmount
from app.domain.runtime_session import RuntimeSession
from app.domain.scheduling_problem import RecipeInstance
from app.runtime.advance_preparation import declare_preparations


def apply_menu(
    session: RuntimeSession, event: RuntimeEvent, knowledge: MenuKnowledgeView
) -> RuntimeSession:
    payload = event.payload
    assert isinstance(payload, MenuPayload)
    details = session.runtime.details
    assert details is not None
    if event.event_type in {"START_SESSION", "ADD_RECIPE"}:
        recipes = {r.recipe_id: r for r in knowledge.recipes}
        menu = list(session.menu)
        lots = list(details.lots)
        preparations = list(details.advance_preparations)
        for index, choice in enumerate(payload.recipes):
            recipe = recipes.get(choice.id)
            if recipe is None or recipe.name != choice.name:
                raise ValueError("菜谱 ID 或名称不属于会话固定版本")
            instance = RecipeInstance(
                recipe_instance_id=RecipeInstanceId(
                    stable_id(
                        "recipe-instance",
                        session.runtime.session_id.root,
                        event.event_id.root,
                        str(index),
                    )
                ),
                recipe_id=choice.id,
                name=choice.name,
            )
            menu.append(instance)
            first_lot = len(lots)
            for material in recipe.ingredient_requirements:
                # 未测量的原料保留原配方一批身份，不把未知量或范围下界当作克数。
                quantity = material.quantity if material.quantity_kind == "EXACT" else None
                amount = (
                    RationalAmount(numerator=quantity.value, denominator=quantity.scale)
                    if quantity
                    else RationalAmount(numerator=1)
                )
                spec = stable_id("material", instance.recipe_instance_id.root, material.spec_id)
                lots.append(
                    LotFact(
                        lot_id=stable_id("raw-lot", session.runtime.session_id.root, spec),
                        spec_id=spec,
                        source_spec_id=material.spec_id,
                        material_spec=next(
                            (
                                item
                                for item in recipe.material_specs
                                if item.spec_id == material.spec_id
                            ),
                            None,
                        ),
                        quantity_kind="EXACT"
                        if quantity
                        else "QUALITATIVE"
                        if material.quantity_kind == "QUALITATIVE"
                        else "RECIPE_BATCH",
                        unit=quantity.unit if quantity else None,
                        produced=amount,
                        available=amount,
                        quality_status="QUALIFIED",
                        produced_at=event.occurred_at,
                        source_event_id=event.event_id.root,
                        availability_evidence=(
                            "MENU_RECIPE_INPUT_DECLARATION",
                            event.event_id.root,
                        ),
                    )
                )
            added_preparations, declared_lots = declare_preparations(
                recipe,
                instance,
                session.policy,
                event,
                session.runtime.time_origin.offset(event.occurred_at),
                tuple(lots[first_lot:]),
            )
            preparations.extend(added_preparations)
            lots[first_lot:] = declared_lots
        return session.model_copy(
            update={
                "menu": tuple(menu),
                "status": "ACTIVE",
                "runtime": session.runtime.model_copy(
                    update={
                        "details": details.model_copy(
                            update={
                                "lots": tuple(lots),
                                "advance_preparations": tuple(preparations),
                            }
                        )
                    }
                ),
            }
        )
    target_instance = next(
        (i for i in session.menu if i.recipe_instance_id == payload.recipe_instance_id), None
    )
    if target_instance is None:
        raise ValueError("菜单实例不存在")
    if target_instance.recipe_instance_id.root in details.cancelled_instance_ids:
        return session
    if event.event_type == "CANCEL_RECIPE":
        recipe = next(r for r in knowledge.recipes if r.recipe_id == target_instance.recipe_id)
        task_ids = {
            stable_id("task", target_instance.recipe_instance_id.root, o.operation_id.root)
            for o in recipe.operations
        }
        completed = {
            t.root
            for e in session.runtime.executions
            if e.status == "COMPLETED"
            for t in e.task_ids
        }
        completed.update(
            task.root for item in active_preparations(session.runtime) for task in item.task_ids
        )
        if task_ids <= completed:
            raise ValueError("已完成的菜不能撤销真实结果")
        details = details.model_copy(
            update={
                "cancelled_instance_ids": (
                    *details.cancelled_instance_ids,
                    target_instance.recipe_instance_id.root,
                )
            }
        )
    else:
        assert payload.earliest_start_sec is not None
        starts = dict(details.earliest_starts)
        starts[target_instance.recipe_instance_id.root] = payload.earliest_start_sec
        details = details.model_copy(update={"earliest_starts": tuple(sorted(starts.items()))})
    return session.model_copy(
        update={"runtime": session.runtime.model_copy(update={"details": details})}
    )
