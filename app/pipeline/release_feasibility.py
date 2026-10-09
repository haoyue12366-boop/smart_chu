"""发布前重新独立校验每道菜，不接受调用方自报 valid=true。"""

from app.domain.knowledge import MenuKnowledgeView, ReleaseRef
from app.domain.release_feasibility import ReleasePlanProof, validate_plan_bindings
from app.pipeline.snapshot_export import SnapshotBuildResult
from app.validation.schedule import ScheduleValidator


def validate_release_plans(
    build: SnapshotBuildResult, proofs: tuple[ReleasePlanProof, ...]
) -> None:
    source = build.snapshot.knowledge
    recipe_ids = tuple(r.recipe_id for r in source.recipes)
    if len(recipe_ids) != 100 or not source.validation.formal_release_eligible:
        raise ValueError("正式发布需要100道人工审核完成的知识")
    validate_plan_bindings(
        proofs,
        recipe_ids=recipe_ids,
        knowledge_version=source.knowledge_version,
        rule_version=source.scope.rule_version,
        snapshot_id=build.snapshot.snapshot_id,
    )
    # 发布前清单尚未生成；验证使用当前快照与来源身份，不依赖未来manifest哈希。
    view = MenuKnowledgeView(
        release=ReleaseRef(
            release_id=source.release_id,
            knowledge_version=source.knowledge_version,
            rule_version=source.scope.rule_version,
            snapshot_id=build.snapshot.snapshot_id,
            manifest_hash="0" * 64,
            release_kind=source.scope.release_kind,
        ),
        snapshot_schema_version=build.snapshot.snapshot_schema_version,
        snapshot_hash=build.snapshot.content_hash,
        recipes=source.recipes,
        devices=source.scope.devices,
        profiles=source.profiles,
        rules=source.rules,
        provenance_index=build.index.evidence,
        device_choices=source.scope.device_choices,
        recipe_contexts=source.scope.recipe_contexts,
    )
    validator = ScheduleValidator()
    for proof in proofs:
        check = validator.validate(view, proof.problem.runtime, proof.problem, proof.candidate)
        if not check.valid or check != proof.validation:
            raise ValueError("正式发布单菜独立校验失败或证明不一致")
