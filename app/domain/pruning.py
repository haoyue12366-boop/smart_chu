"""候选删除的可审计替换证据，与求解器统计分离。"""

from typing import Literal, Self

from pydantic import model_validator

from app.domain.base import FrozenModel, NonEmpty
from app.domain.ids import CarrierId


class PruningContext(FrozenModel):
    knowledge_version: NonEmpty
    rule_version: NonEmpty
    policy_version: NonEmpty
    state_dependency_hash: NonEmpty
    protected_carrier_ids: tuple[CarrierId, ...] = ()


class PruningRecord(FrozenModel):
    removed_candidate_id: CarrierId
    replacement_candidate_id: CarrierId | None
    kind: Literal["EQUIVALENT", "BUDGET_TRUNCATION"] = "EQUIVALENT"
    proof_rule_id: Literal["identical-except-carrier-id-v1", "optional-limit-v1"] = (
        "identical-except-carrier-id-v1"
    )
    assumptions: tuple[NonEmpty, ...] = ("候选身份未被运行事实或外部承诺引用",)
    preserved_time_and_material_ports: bool = True
    context: PruningContext | None = None

    @model_validator(mode="after")
    def evidence_kind(self) -> Self:
        if self.kind == "EQUIVALENT":
            if (
                self.replacement_candidate_id is None
                or self.proof_rule_id != "identical-except-carrier-id-v1"
                or not self.preserved_time_and_material_ports
            ):
                raise ValueError("等价删除必须保留完整替换证明")
        elif (
            self.replacement_candidate_id is not None
            or self.proof_rule_id != "optional-limit-v1"
            or self.preserved_time_and_material_ports
        ):
            raise ValueError("预算截断不能伪称存在等价替代")
        return self
