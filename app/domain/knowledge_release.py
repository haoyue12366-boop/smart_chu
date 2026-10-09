"""发布输入绑定具体校验内容；development 始终显式保留待审性质。"""

from typing import Self

from pydantic import model_validator

from app.domain.base import FrozenModel, NonEmpty, content_hash
from app.domain.canonical_recipe import CanonicalRecipeModel
from app.domain.knowledge import KnowledgeValidationReport, ReleaseScope, knowledge_input_hash
from app.domain.processing_rules import ProcessingRule
from app.domain.provenance import ArtifactRef, ProvenanceRecord
from app.domain.resources import DeviceProfile


class ReviewedRelease(FrozenModel):
    schema_version: NonEmpty = "1.0"
    release_id: NonEmpty
    knowledge_version: NonEmpty
    recipes: tuple[CanonicalRecipeModel, ...]
    profiles: tuple[DeviceProfile, ...]
    rules: tuple[ProcessingRule, ...] = ()
    scope: ReleaseScope
    provenance: tuple[ProvenanceRecord, ...]
    source_artifacts: tuple[ArtifactRef, ...]
    validation: KnowledgeValidationReport

    @model_validator(mode="after")
    def checked_content(self) -> Self:
        if not self.validation.valid or self.validation.violations:
            raise ValueError("发布输入尚未通过知识校验")
        if self.validation.release_kind != self.scope.release_kind:
            raise ValueError("发布类型与知识校验不一致")
        if self.validation.input_hash != knowledge_input_hash(
            self.recipes, self.profiles, self.rules, self.scope
        ):
            raise ValueError("发布内容与校验输入哈希不匹配")
        if {p.provenance_id for p in self.provenance} != set(self.scope.evidence_ids):
            raise ValueError("发布证据索引不完整")
        return self

    @property
    def content_hash(self) -> str:
        return content_hash(self)
