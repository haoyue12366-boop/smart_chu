from typing import Self

from pydantic import model_validator

from app.domain.base import Digest, FrozenModel, NonEmpty


class ValidationViolation(FrozenModel):
    code: NonEmpty
    message: NonEmpty
    entity_refs: tuple[NonEmpty, ...] = ()
    evidence_refs: tuple[NonEmpty, ...] = ()


class ValidationReport(FrozenModel):
    report_id: NonEmpty
    problem_hash: Digest
    candidate_hash: Digest
    validator_version: NonEmpty
    valid: bool
    violations: tuple[ValidationViolation, ...] = ()

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if self.valid != (not self.violations):
            raise ValueError("校验结论与违规清单不一致")
        return self
