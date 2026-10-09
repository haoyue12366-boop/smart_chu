"""内容绑定的全组规则；委托估计与人工批准分别保留。"""

from typing import Literal, Self

from pydantic import model_validator

from app.domain.base import Digest, FrozenModel, NonEmpty, PositiveInt
from app.domain.ids import OperationId, RecipeId, TaskId
from app.domain.knowledge import MenuKnowledgeView
from app.domain.runtime_snapshot import RuntimeSnapshot
from app.domain.scheduling_problem import RecipeInstance


class GroupBinding(FrozenModel):
    recipe_id: RecipeId
    operation_id: OperationId
    recipe_hash: Digest
    operation_hash: Digest
    processing_spec: NonEmpty
    configuration_keys: tuple[NonEmpty, ...]


class GroupRuleSpec(FrozenModel):
    schema_version: Literal["group-rule-v1"] = "group-rule-v1"
    bindings: tuple[GroupBinding, ...]
    duration_sec: PositiveInt
    authority: Literal["HUMAN_REVIEWED", "DELEGATED_DEVELOPMENT_ESTIMATE"]
    authorization_ref: NonEmpty
    scope_note: NonEmpty
    thermal_model: Literal["COLD_LOAD_SETUP_PREHEAT_HEAT_STOP_UNLOAD"] | None = None

    @model_validator(mode="after")
    def exact_scope(self) -> Self:
        identities = {(b.recipe_id, b.operation_id) for b in self.bindings}
        if len(self.bindings) < 2 or len(identities) != len(self.bindings):
            raise ValueError("共享审核须绑定至少两个不重复的菜谱/工序")
        if any(not b.configuration_keys for b in self.bindings):
            raise ValueError("每个成员须有明确允许配置")
        return self


class GroupContext(FrozenModel):
    knowledge: MenuKnowledgeView
    runtime: RuntimeSnapshot
    menu: tuple[RecipeInstance, ...]
    allow_delegated_estimates: bool = False
    unknown_material_task_ids: tuple[TaskId, ...] = ()
