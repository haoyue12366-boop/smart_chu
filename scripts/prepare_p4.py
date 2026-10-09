"""将明确授权、逐菜复核及 P4 输入绑定；不冒充人工实测或实现动态运行。"""

import argparse
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

from app.domain.canonical_recipe import CanonicalRecipeModel, ReviewStamp
from app.domain.knowledge_release import ReviewedRelease
from app.domain.provenance import ArtifactRef, ProvenanceRecord
from app.knowledge.loader import LoadedRelease, load_release, read_release_ref
from app.validation.knowledge import validate_knowledge

ROOT = Path(__file__).resolve().parents[1]
BASE_ROOT = Path("data/preparations/p3-thermal-v1/releases")
BASE_ID = "development-v3-p3-thermal-v1-all"
OUTPUT = Path("data/preparations/p4-v1")
VERSION = "delegated-v3-p4-preparation-v1"
AUTH_PATH = Path("data/development/authorizations/p4-delegation-v1.json")
AUDIT_ID = "p4-data-audit-v1"


def write_immutable(path: Path, value: object) -> None:
    payload = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise ValueError("不能覆盖不同的准备版本：" + str(path))
        return
    with path.open("xb") as stream:
        stream.write(payload)


def artifact(path: Path, root: Path = ROOT) -> ArtifactRef:
    return ArtifactRef(
        path=path.relative_to(root).as_posix(),
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        media_type="application/json" if path.suffix == ".json" else "text/plain",
    )


def audit_release(base: LoadedRelease) -> dict:
    source = base.snapshot.knowledge
    validation = validate_knowledge(source.recipes, source.profiles, source.rules, source.scope)
    if not validation.valid or len(source.recipes) != 100:
        raise ValueError("完整 100 菜知识校验未通过")
    if {r.recipe_id for r in source.recipes} != set(source.scope.expected_recipe_ids):
        raise ValueError("固定菜谱身份覆盖不完整")
    rows, conflicts = [], []
    for recipe in source.recipes:
        for op in recipe.operations:
            op.check_approved()
            if op.action == "MARINATE" and any(
                u.resource_type == "HUMAN" for u in op.resource_requirements
            ):
                conflicts.append([recipe.recipe_id.root, op.operation_id.root])
        rows.append(
            {
                "recipe_id": recipe.recipe_id.root,
                "name": recipe.name,
                "semantic_hash": recipe.semantic_hash(),
                "operation_count": len(recipe.operations),
                "decision": "APPROVED_FOR_P4_BY_DELEGATION",
                "checks": [
                    "SCHEMA",
                    "DAG_AND_MATERIAL_PATH",
                    "DURATION_AND_SOURCE",
                    "PHYSICAL_DEVICE_MAPPING",
                    "SINGLE_HUMAN",
                    "PASSIVE_MARINATION",
                ],
            }
        )
    if conflicts:
        raise ValueError("腌制等待仍占用人工，须先修复：" + str(conflicts))
    return {
        "audit_id": AUDIT_ID,
        "valid": True,
        "base_content_hash": source.content_hash,
        "base_release": base.manifest.release_id,
        "recipe_count": len(rows),
        "operation_count": sum(len(r.operations) for r in source.recipes),
        "marinate_count": sum(o.action == "MARINATE" for r in source.recipes for o in r.operations),
        "marinate_human_conflicts": conflicts,
        "process_changes": 0,
        "rule_decisions": [
            {"rule_id": r.rule_id, "decision": "APPROVED_FOR_P4_BY_DELEGATION"}
            for r in source.rules
        ],
        "profile_count": len(source.profiles),
        "accepted_limitations": [
            "时长与批次参数按现有来源作为计划估计接受，不是现场实测。",
            "定性物料可用于整份菜谱排程；不能凭定性整批换算可分配克数或安全保存期。",
            "只接受现有 S02/H02 共享规则；缺少依据的温差转换、跨批复用及失败续做默认关闭。",
            "旧历史来源缺口保留，固定 V3 原文及现有归档作为本版来源。",
            "APPROVED 表示本次授权范围内的路径审核通过；不表示官方协议验收或食品工艺实测。",
        ],
        "recipes": rows,
    }


def approve_recipes(
    base: LoadedRelease, auth: dict, audit: dict
) -> tuple[CanonicalRecipeModel, ...]:
    source = base.snapshot.knowledge
    if (
        auth.get("source") != "USER_MESSAGE"
        or auth.get("actor_kind") != "DELEGATED_AGENT"
        or auth.get("approve_for_p4") is not True
        or auth.get("base_content_hash") != source.content_hash
        or not auth.get("user_instruction")
        or not auth.get("authorization_id")
        or auth.get("reviewer") != "Codex（用户授权代理审核）"
    ):
        raise ValueError("缺少绑定当前内容的用户代理审核授权")
    if audit != audit_release(base):
        raise ValueError("审核证据已改变或与当前内容不一致")
    approved = []
    for recipe in source.recipes:
        review_id = "p4-delegated:" + recipe.recipe_id.root
        document = recipe.model_dump(mode="json")
        document.update(
            review_status="APPROVED",
            review_patch_refs=[*recipe.review_patch_refs, review_id],
            approval=ReviewStamp(
                review_id=review_id,
                reviewer=auth["reviewer"],
                reviewed_at=datetime.fromisoformat(auth["recorded_at"]),
                evidence_refs=(auth["authorization_id"], AUDIT_ID),
                approved_content_hash=recipe.semantic_hash(),
            ).model_dump(mode="json"),
        )
        result = CanonicalRecipeModel.model_validate(document)
        if result.semantic_hash() != recipe.semantic_hash():
            raise ValueError("审核意外改变菜谱工艺")
        approved.append(result)
    return tuple(approved)


def prepare(root: Path = ROOT) -> ReviewedRelease:
    base = load_release(root / BASE_ROOT, read_release_ref(root / BASE_ROOT, BASE_ID))
    source = base.snapshot.knowledge
    auth = json.loads((root / AUTH_PATH).read_bytes())
    audit = audit_release(base)
    recipes = approve_recipes(base, auth, audit)
    # 原始 V3 身份须同时与工作区和旧发布归档匹配；不用旧 docs 副本替代。
    csv_ref = next(a for a in source.source_artifacts if a.path.endswith("recipes_100_调度版.csv"))
    csv_ref.verify(root)
    audit_path = root / OUTPUT / "data_review.json"
    write_immutable(audit_path, audit)
    new_artifacts = tuple(
        artifact(root / p, root)
        for p in (
            AUTH_PATH,
            OUTPUT / "data_review.json",
            Path("scripts/prepare_p4.py"),
        )
    )
    added_provenance = tuple(
        ProvenanceRecord(
            provenance_id=ident,
            origin=origin,
            source_file=ref.path,
            source_hash=ref.sha256,
            record_id=ident,
            field_path=field,
            text_span=note,
            artifact_ref=ref,
        )
        for ident, origin, ref, field, note in (
            (
                auth["authorization_id"],
                "SOURCE_EXPLICIT",
                new_artifacts[0],
                "/user_instruction",
                auth["user_instruction"],
            ),
            (
                AUDIT_ID,
                "MODEL_SUGGESTION",
                new_artifacts[1],
                "/recipes",
                "用户授权 Codex 代理审核通过，用于 P4；不宣称人工逐项检查或实测。",
            ),
        )
    )
    provenance = (*source.provenance, *added_provenance)
    by_id = {r.recipe_id: r for r in recipes}
    scope = source.scope.model_copy(
        update={
            "required_recipes": tuple(by_id[r.recipe_id] for r in source.scope.required_recipes),
            "evidence_ids": tuple(p.provenance_id for p in provenance),
        }
    )
    updated = ReviewedRelease(
        release_id=VERSION + "-all",
        knowledge_version=VERSION,
        recipes=recipes,
        profiles=source.profiles,
        rules=source.rules,
        scope=scope,
        provenance=provenance,
        source_artifacts=(*source.source_artifacts, *new_artifacts),
        validation=validate_knowledge(recipes, source.profiles, source.rules, scope),
    )
    old_paths = {a.path for a in source.source_artifacts}
    for ref in updated.source_artifacts:
        origin = root / BASE_ROOT / BASE_ID if ref.path in old_paths else root
        payload = ref.verify(origin).read_bytes()
        target = root / OUTPUT / "source_inputs" / ref.path
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            ref.verify(root / OUTPUT / "source_inputs")
        else:
            with target.open("xb") as stream:
                stream.write(payload)
    write_immutable(root / OUTPUT / "reviewed_release.json", updated.model_dump(mode="json"))
    return updated


def publish(source: ReviewedRelease) -> None:
    from neo4j import GraphDatabase

    from app.knowledge.graph_projection import GraphProjector
    from app.pipeline.publish import ReleaseBundle, publish_release
    from app.pipeline.snapshot_export import SnapshotExporter

    pointer = ROOT / "data/releases/active_release.json"
    pointer_bytes = pointer.read_bytes()
    settings = dict(
        line.split("=", 1)
        for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    )
    settings.update(os.environ)
    with GraphDatabase.driver(
        settings["NEO4J_URI"],
        auth=(
            settings["NEO4J_USERNAME"],
            settings["NEO4J_PASSWORD"],
        ),
    ) as driver:
        driver.verify_connectivity()
        projector = GraphProjector(driver)
        graph = projector.project(source)
        build = SnapshotExporter(projector).export_snapshot(source)
    output = ROOT / OUTPUT
    ref = publish_release(
        ReleaseBundle(build=build, source_root=output / "source_inputs"), output / "releases"
    )
    loaded = load_release(output / "releases", ref)
    if loaded.snapshot.knowledge != source or pointer.read_bytes() != pointer_bytes:
        raise ValueError("发布重载或旧运行指针校验失败")
    write_immutable(
        output / "publication_report.json",
        {
            "status": "P4_DELEGATED_REVIEW_PUBLISHED",
            "release": ref.model_dump(mode="json"),
            "graph": graph.model_dump(mode="json"),
            "offline_reload_valid": True,
            "approved_recipe_count": len(source.recipes),
            "authority": "DELEGATED_AGENT",
            "original_active_release_unchanged": True,
            "physical_measurement_performed": False,
            "competition_protocol_verified": False,
            "p4_runtime_implemented": False,
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--verify-all", action="store_true")
    args = parser.parse_args()
    source = prepare()
    print(f"代理审核通过：{len(source.recipes)} 菜；工艺未改变", flush=True)
    if args.publish:
        publish(source)
        print("真实 Neo4j 发布与文件重载通过", flush=True)
    if args.verify_all:
        from scripts.verify_all_recipes import verify_all

        report = verify_all(
            ROOT / OUTPUT / "releases",
            source.release_id,
            ROOT / "benchmarks/reports/P4-preparation-all-recipes",
        )
        return 0 if report["success_count"] == report["recipe_count"] == 100 else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
