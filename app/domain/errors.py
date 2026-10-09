from enum import StrEnum

from app.domain.base import FrozenModel, NonEmpty


class ErrorCode(StrEnum):
    INVALID_REQUEST = "INVALID_REQUEST"
    UNKNOWN_RECIPE = "UNKNOWN_RECIPE"
    RECIPE_NAME_MISMATCH = "RECIPE_NAME_MISMATCH"
    DATA_NOT_READY = "DATA_NOT_READY"
    STATE_CONFLICT = "STATE_CONFLICT"
    STATE_INCOMPLETE = "STATE_INCOMPLETE"
    NO_FEASIBLE_PLAN = "NO_FEASIBLE_PLAN"
    SOLVER_MODEL_ERROR = "SOLVER_MODEL_ERROR"
    SERVICE_NOT_READY = "SERVICE_NOT_READY"


class DomainError(FrozenModel):
    code: ErrorCode
    message: NonEmpty
    evidence_refs: tuple[NonEmpty, ...] = ()


class ServiceError(ValueError):
    """应用层可预期失败；HTTP 状态由协议层映射。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message
