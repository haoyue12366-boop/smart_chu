"""正式发布的全路径单菜证据，绑定问题、候选及独立校验身份。"""

from typing import Self

from pydantic import model_validator

from app.domain.base import FrozenModel
from app.domain.ids import RecipeId
from app.domain.schedule import CandidateSchedule
from app.domain.scheduling_problem import SchedulingProblem
from app.domain.validation_contract import ValidationReport


class ReleasePlanProof(FrozenModel):
    problem: SchedulingProblem
    candidate: CandidateSchedule
    validation: ValidationReport

    @model_validator(mode="after")
    def bound(self) -> Self:
        if (
            not self.validation.valid
            or self.problem.problem_hash != self.candidate.problem_hash
            or self.validation.problem_hash != self.problem.problem_hash
            or self.validation.candidate_hash != self.candidate.candidate_hash
        ):
            raise ValueError("单菜计划证据身份不一致或校验失败")
        if (
            len(self.problem.recipe_instances) != 1
            or self.problem.fixed_executions
            or self.problem.runtime.executions
            or self.problem.runtime.material_lots
            or self.problem.runtime.now_offset_sec
        ):
            raise ValueError("发布证据必须为从零开始的完整单菜计划")
        return self


def validate_plan_bindings(
    proofs: tuple[ReleasePlanProof, ...],
    *,
    recipe_ids: tuple[RecipeId, ...],
    knowledge_version: str,
    rule_version: str,
    snapshot_id: str,
) -> None:
    actual = tuple(p.problem.recipe_instances[0].recipe_id for p in proofs)
    if len(set(actual)) != len(actual) or set(actual) != set(recipe_ids):
        raise ValueError("发布单菜计划缺失、重复或存在额外ID")
    if any(
        (p.problem.knowledge_version, p.problem.rule_version, p.problem.snapshot_id)
        != (knowledge_version, rule_version, snapshot_id)
        for p in proofs
    ):
        raise ValueError("发布单菜计划与知识版本不一致")
