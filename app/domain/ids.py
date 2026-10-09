"""不同层身份在内存中保留类型，线上 JSON 仍是字符串。"""

from __future__ import annotations

from pydantic import ConfigDict, RootModel, model_validator

from app.domain.base import FrozenModel, NonEmpty


class SemanticId(RootModel[NonEmpty]):
    model_config = ConfigDict(frozen=True)

    @model_validator(mode="before")
    @classmethod
    def reject_other_identity(cls, value: object) -> object:
        if isinstance(value, SemanticId) and type(value) is not cls:
            raise ValueError("不同类型的身份不能互换")
        return value


class RecipeId(SemanticId):
    pass


class RecipeInstanceId(SemanticId):
    pass


class OperationId(SemanticId):
    pass


class TaskId(SemanticId):
    pass


class CarrierId(SemanticId):
    pass


class ExecutionId(SemanticId):
    pass


class MaterialLotId(SemanticId):
    pass


class EventId(SemanticId):
    pass


class SessionId(SemanticId):
    pass


class VersionRef(FrozenModel):
    schema_version: NonEmpty
    knowledge_version: NonEmpty
    rule_version: NonEmpty
