"""菜谱与局部依赖图由固定快照读取。"""

from fastapi import APIRouter, Request

from app.api.dependencies import container
from app.api.errors import ApiError

router = APIRouter(prefix="/api/v1/recipes", tags=["knowledge"])


@router.get("")
def catalog(request: Request) -> dict[str, object]:
    services = container(request)
    knowledge = services.knowledge
    return {
        "knowledge_version": knowledge.release.knowledge_version,
        "recipes": [
            {
                "recipe_id": recipe.recipe_id.root,
                "name": recipe.name,
                "operation_count": len(recipe.operations),
                "ingredient_names": list(
                    dict.fromkeys(spec.name for spec in recipe.material_specs)
                )[:6],
            }
            for recipe in knowledge.recipes
        ],
        "devices": [device.model_dump(mode="json") for device in knowledge.devices],
        "timezone": services.settings.timezone,
        "language_enabled": services.intents.enabled,
    }


@router.get("/{recipe_id}/graph")
def graph(recipe_id: str, request: Request, session_id: str | None = None) -> dict[str, object]:
    services = container(request)
    knowledge = services.knowledge_for(session_id) if session_id else services.knowledge
    recipe = next((r for r in knowledge.recipes if r.recipe_id.root == recipe_id), None)
    if recipe is None:
        raise ApiError("UNKNOWN_RECIPE", "发布中不存在该菜谱", 404)
    return {
        "recipe_id": recipe_id,
        "name": recipe.name,
        "knowledge_version": knowledge.release.knowledge_version,
        "operations": [item.model_dump(mode="json") for item in recipe.operations],
        "dependencies": [item.model_dump(mode="json") for item in recipe.dependencies],
        "materials": [item.model_dump(mode="json") for item in recipe.material_specs],
    }
