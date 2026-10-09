"""将逐菜审核的固定锚点发布为新知识版本；不在运行时猜测最后加热步骤。"""

import json
import shutil
from pathlib import Path

from app.config import ROOT
from app.domain.base import ReviewStatus
from app.domain.cooking_completion import CookingCompletionRule
from app.domain.knowledge_release import ReviewedRelease
from app.domain.provenance import ArtifactRef, ProvenanceOrigin, ProvenanceRecord
from app.knowledge.loader import load_release, read_release_ref
from app.pipeline.publish import ReleaseBundle, publish_release
from app.pipeline.snapshot_export import export_canonical_snapshot
from app.validation.knowledge import validate_knowledge

RELEASE_ID = "delegated-v3-cook-finish-v1-all"


def main() -> None:
    import hashlib

    release_root = ROOT / "data/preparations/p4-v1/releases"
    old_id = "delegated-v3-p4-preparation-v1-all"
    old = load_release(release_root, read_release_ref(release_root, old_id))
    source = old.snapshot.knowledge
    review_path = Path("data/preparations/cook-finish-v1/review.json")
    payload = (ROOT / review_path).read_bytes()
    review = json.loads(payload)
    if review["source_snapshot_id"] != old.snapshot.snapshot_id:
        raise ValueError("审核来源快照不匹配")
    records = {r["recipe_id"]: r for r in review["recipes"]}
    if len(records) != 100 or set(records) != {r.recipe_id.root for r in source.recipes}:
        raise ValueError("审核必须精确覆盖固定100菜")
    artifact = ArtifactRef(
        path=review_path.as_posix(),
        sha256=hashlib.sha256(payload).hexdigest(),
        media_type="application/json",
    )
    contexts = {c.recipe_id: c for c in source.scope.recipe_contexts}
    additions = []
    for recipe in source.recipes:
        row = records[recipe.recipe_id.root]
        if row["recipe_semantic_hash"] != recipe.semantic_hash():
            raise ValueError("菜谱语义已改变，需要重新审核")
        operation_map = {o.operation_id.root: o for o in recipe.operations}
        for operation in row["anchors"]:
            if operation_map[operation["operation_id"]].description != operation["description"]:
                raise ValueError("锚点原文与审核记录不符")
        evidence_id = "cook-finish-v1:" + recipe.recipe_id.root
        rule = CookingCompletionRule(
            operation_ids=tuple(o["operation_id"] for o in row["anchors"]),
            kind=row["kind"],
            evidence_refs=(evidence_id,),
        )
        contexts[recipe.recipe_id] = contexts[recipe.recipe_id].model_copy(
            update={"cooking_completion": rule}
        )
        additions.append(
            ProvenanceRecord(
                provenance_id=evidence_id,
                origin=ProvenanceOrigin.SOURCE_DERIVED,
                source_file=artifact.path,
                source_hash=artifact.sha256,
                record_id=recipe.recipe_id.root,
                field_path="cooking_completion",
                text_span=row["rationale"],
                artifact_ref=artifact,
                review_status=ReviewStatus.APPROVED,
                approved_review_ref="DELEGATED_AGENT:2026-10-07:cook-finish-v1",
            )
        )
    scope = source.scope.model_copy(
        update={
            "recipe_contexts": tuple(contexts.values()),
            "evidence_ids": (*source.scope.evidence_ids, *(p.provenance_id for p in additions)),
        }
    )
    validation = validate_knowledge(source.recipes, source.profiles, source.rules, scope)
    updated = ReviewedRelease(
        release_id=RELEASE_ID,
        knowledge_version="delegated-v3-cook-finish-v1",
        recipes=source.recipes,
        profiles=source.profiles,
        rules=source.rules,
        scope=scope,
        provenance=(*source.provenance, *additions),
        source_artifacts=(*source.source_artifacts, artifact),
        validation=validation,
    )
    build = export_canonical_snapshot(updated)
    staging = ROOT / "data/preparations/cook-finish-v1/source-inputs"
    for ref in source.source_artifacts:
        target = staging / ref.path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ref.verify(release_root / old_id), target)
    target = staging / artifact.path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    ref = publish_release(
        ReleaseBundle(
            build=build,
            source_root=staging,
            replays=old.manifest.replays,
            archive_root=release_root / old_id / "extraction",
        ),
        release_root,
    )
    print(ref.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
