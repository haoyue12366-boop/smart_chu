"""从不可变P2来源准备P3输入；不修改原发布，也不授予人工批准。"""

import hashlib
from pathlib import Path

from pydantic import JsonValue

from app.domain.base import Digest, FrozenModel, NonEmpty, content_hash
from app.domain.canonical_recipe import CanonicalRecipeModel
from app.domain.compatibility import GroupRuleSpec
from app.domain.knowledge_release import ReviewedRelease
from app.domain.processing_rules import ProcessingRule
from app.domain.provenance import ArtifactRef, ProvenanceRecord
from app.knowledge.loader import load_release, read_release_ref
from app.validation.knowledge import validate_knowledge

VERSION = "development-v3-p3-preparation-v1"
RULE_VERSION = "development-shared-v1"
BASE_RELEASE = "development-v3-rebased-v2-all"


class ReviewDecision(FrozenModel):
    id: NonEmpty
    decision: NonEmpty
    reason: NonEmpty
    parameters: dict[str, JsonValue]
    origin: NonEmpty
    measured: bool


class DelegatedReport(FrozenModel):
    review_id: NonEmpty
    actor_kind: NonEmpty
    authorization_ref: NonEmpty
    source_knowledge_version: NonEmpty
    source_snapshot_hash: Digest
    worksheet_sha256: Digest
    review_scope: NonEmpty
    formal_human_review_complete: bool
    runtime_rules_enabled: bool
    decisions: tuple[ReviewDecision, ...]
    processing_rules: tuple[ProcessingRule, ...]


def _artifact(root: Path, path: Path) -> ArtifactRef:
    path = path.resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("准备包来源须位于项目内以便归档")
    return ArtifactRef(
        path=path.relative_to(root.resolve()).as_posix(),
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        media_type="application/json" if path.suffix == ".json" else "text/x-python",
    )


def _reversion(recipe: CanonicalRecipeModel) -> CanonicalRecipeModel:
    if recipe.approval is not None:
        raise ValueError("技术版本迁移不能自动沿用原人工签名")
    operations = tuple(
        o.model_copy(
            update={
                "resource_requirements": tuple(
                    u.model_copy(update={"rule_version": RULE_VERSION})
                    for u in o.resource_requirements
                )
            }
        )
        for o in recipe.operations
    )
    return CanonicalRecipeModel.model_validate(
        recipe.model_copy(
            update={
                "recipe_version": recipe.recipe_version + "+p3-preparation-v1",
                "operations": operations,
            }
        )
    )


def prepare_p3_release(
    root: Path, *, review_path: Path | None = None
) -> tuple[ReviewedRelease, dict[str, object]]:
    baseline = load_release(
        root / "data/releases", read_release_ref(root / "data/releases", BASE_RELEASE)
    )
    old = baseline.snapshot.knowledge
    path = review_path or root / "data/development/p3-delegated-review-v2.json"
    report = DelegatedReport.model_validate_json(path.read_bytes())
    if (
        report.source_snapshot_hash != baseline.snapshot.content_hash
        or report.source_knowledge_version != old.knowledge_version
    ):
        raise ValueError("委托复核与当前来源快照不一致")
    if (
        report.actor_kind != "MODEL"
        or report.formal_human_review_complete
        or report.runtime_rules_enabled
    ):
        raise ValueError("委托准备不能伪造人工审核或已启用状态")
    worksheet = root / "data/issues/p3_rule_review_evidence.json"
    if hashlib.sha256(worksheet.read_bytes()).hexdigest() != report.worksheet_sha256:
        raise ValueError("审核工作单来源哈希失效")
    by_id = {r.recipe_id: r for r in old.recipes}
    recipes = tuple(_reversion(r) for r in old.recipes)
    new_by_id = {r.recipe_id: r for r in recipes}
    decisions = {d.id: d for d in report.decisions}
    expected = {
        f"P3-{kind}{i:02}"
        for kind, count in (("S", 4), ("H", 4), ("T", 5))
        for i in range(1, count + 1)
    }
    if set(decisions) != expected or len(decisions) != len(report.decisions):
        raise ValueError("13项委托结论不完整或身份重复")
    rules = []
    for rule in report.processing_rules:
        ident = rule.rule_id.removesuffix("-delegated-v1")
        decision = decisions.get(ident)
        spec = GroupRuleSpec.model_validate_json(rule.group_compatibility_predicate)
        if (
            decision is None
            or decision.decision != "DEVELOPMENT_ESTIMATE_ACCEPTED"
            or spec.authority != "DELEGATED_DEVELOPMENT_ESTIMATE"
            or spec.authorization_ref != report.authorization_ref
            or rule.review_status != "NEEDS_REVIEW"
            or rule.rule_version != old.scope.rule_version
            or report.authorization_ref not in rule.evidence_refs
        ):
            raise ValueError("委托规则身份、许可或审核性质不一致")
        new_bindings = []
        for binding in spec.bindings:
            recipe = by_id.get(binding.recipe_id)
            if recipe is None or binding.recipe_hash != recipe.semantic_hash():
                raise ValueError("规则引用未知菜谱或过期内容")
            operation = next(
                (o for o in recipe.operations if o.operation_id == binding.operation_id), None
            )
            if operation is None or content_hash(operation) != binding.operation_hash:
                raise ValueError("规则工序内容已变化")
            new_recipe = new_by_id[binding.recipe_id]
            new_op = next(
                o for o in new_recipe.operations if o.operation_id == binding.operation_id
            )
            new_bindings.append(
                binding.model_copy(
                    update={
                        "recipe_hash": new_recipe.semantic_hash(),
                        "operation_hash": content_hash(new_op),
                    }
                )
            )
        rules.append(
            rule.model_copy(
                update={
                    "rule_version": RULE_VERSION,
                    "group_compatibility_predicate": spec.model_copy(
                        update={"bindings": tuple(new_bindings)}
                    ).model_dump_json(),
                }
            )
        )
    if len(rules) != 2 or {r.rule_id for r in rules} != {
        "P3-S02-delegated-v1",
        "P3-H02-delegated-v1",
    }:
        raise ValueError("首批准备范围只能为S02和H02")
    authorization = root / "data/development/authorizations/p3-delegation-v1.json"
    # 授权记录只证明来源；不据此将模型判断标成人工或实测。
    import json

    auth = json.loads(authorization.read_text(encoding="utf-8"))
    if (
        auth.get("authorization_id") != report.authorization_ref
        or auth.get("source") != "USER_MESSAGE"
    ):
        raise ValueError("委托授权记录不匹配")
    added_artifacts = tuple(
        _artifact(root, p)
        for p in (path, authorization, worksheet, root / "app/pipeline/p3_preparation.py")
    )
    review_ref, auth_ref = added_artifacts[:2]
    provenance = [
        *old.provenance,
        ProvenanceRecord(
            provenance_id=report.authorization_ref,
            origin="EXTERNAL_EVIDENCE",
            source_file=auth_ref.path,
            source_hash=auth_ref.sha256,
            record_id=report.authorization_ref,
            field_path="/user_instruction",
            text_span=auth["user_instruction"],
            artifact_ref=auth_ref,
        ),
    ]
    for i, rule in enumerate(rules):
        decision = decisions[rule.rule_id.removesuffix("-delegated-v1")]
        provenance.append(
            ProvenanceRecord(
                provenance_id=report.review_id + ":" + decision.id,
                origin="MODEL_SUGGESTION",
                source_file=review_ref.path,
                source_hash=review_ref.sha256,
                record_id=decision.id,
                field_path=f"/processing_rules/{i}",
                text_span=decision.reason,
                artifact_ref=review_ref,
            )
        )
    scope = old.scope.model_copy(
        update={
            "rule_version": RULE_VERSION,
            "devices": tuple(
                d.model_copy(update={"rule_version": RULE_VERSION}) for d in old.scope.devices
            ),
            "required_recipes": tuple(new_by_id[r.recipe_id] for r in old.scope.required_recipes),
            "evidence_ids": tuple(p.provenance_id for p in provenance),
        }
    )
    profiles = tuple(p.model_copy(update={"rule_version": RULE_VERSION}) for p in old.profiles)
    validation = validate_knowledge(recipes, profiles, tuple(rules), scope)
    source = ReviewedRelease(
        release_id=VERSION + "-all",
        knowledge_version=VERSION,
        recipes=recipes,
        profiles=profiles,
        rules=tuple(rules),
        scope=scope,
        provenance=tuple(provenance),
        source_artifacts=(*old.source_artifacts, *added_artifacts),
        validation=validation,
    )
    bindings = []
    for before, after in zip(old.recipes, source.recipes, strict=True):
        # 唯一允许差异：技术规则版本和菜谱版本，不能顺便修改工艺参数。
        restored = after.model_copy(
            update={
                "recipe_version": before.recipe_version,
                "operations": tuple(
                    a.model_copy(
                        update={
                            "resource_requirements": tuple(
                                ua.model_copy(update={"rule_version": ub.rule_version})
                                for ub, ua in zip(
                                    b.resource_requirements, a.resource_requirements, strict=True
                                )
                            )
                        }
                    )
                    for b, a in zip(before.operations, after.operations, strict=True)
                ),
            }
        )
        if restored != before:
            raise ValueError("准备包技术迁移意外改变原工艺")
        bindings.append(
            {
                "recipe_id": before.recipe_id.root,
                "before": before.semantic_hash(),
                "after": after.semantic_hash(),
            }
        )
    audit: dict[str, object] = {
        "base_release_id": old.release_id,
        "base_snapshot_hash": baseline.snapshot.content_hash,
        "prepared_release_id": source.release_id,
        "prepared_content_hash": source.content_hash,
        "recipe_count": len(recipes),
        "operation_count": sum(len(r.operations) for r in recipes),
        "process_changes": 0,
        "technical_version_migration": True,
        "review_decision_count": len(decisions),
        "shared_runtime_enabled": False,
        "formal_human_review_complete": False,
        "recipe_bindings": bindings,
    }
    return source, audit
