"""将完善版12道真实样本接入P0待审契约；从不生成审核批准。"""

from __future__ import annotations

import hashlib
import json
import re
from fractions import Fraction
from pathlib import Path

from app.domain.canonical_recipe import CanonicalRecipeModel
from app.domain.provenance import ProvenanceRecord
from app.pipeline.issues import DataIssue

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests/fixtures/fixture_manifest.json"
REVISION = ROOT / "data/development/development-v3-rebased-v1/dataset.json"
CSV = ROOT / "docs/recipes_100_详细步骤_完善版.csv"
MAP = ROOT / "data/issues/p0_process_map.json"
VERSION = "development-v3-rebased-v1"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, content: object) -> None:
    path.write_text(json.dumps(content, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def provenance(ref: str, path: Path, rid: str, field: str, text: str, step=None) -> dict:
    return ProvenanceRecord(
        provenance_id=ref,
        origin="SOURCE_EXPLICIT" if path.name == "recipes_100.csv" else "SOURCE_DERIVED",
        source_file=path.relative_to(ROOT).as_posix(),
        source_hash=sha(path),
        record_id=rid,
        field_path=field,
        step_number=step,
        text_span=text,
        artifact_ref={
            "path": path.relative_to(ROOT).as_posix(),
            "sha256": sha(path),
            "media_type": "text/csv" if path.suffix == ".csv" else "application/json",
        },
    ).model_dump(mode="json")


def ingredients(sample: dict, evidence: list[dict]) -> tuple[list[dict], list[dict]]:
    rid = sample["recipe_id"]
    specs, requirements = [], []
    unit_map = {"克": "g", "千克": "kg", "毫升": "ml", "升": "L"}
    units = {"g", "kg", "ml", "L", "个", "片", "根", "张", "瓣", "朵", "汤匙", "茶匙"}
    number = r"\d+(?:\.\d+)?(?:/\d+)?"
    expression = re.compile(
        rf"^(.*?)({number})(?:\s*[~～–-]\s*({number}))?"
        r"\s*(千克|毫升|汤匙|茶匙|kg|ml|克|升|g|L|个|片|根|张|瓣|朵)(.*)$"
    )
    for index, raw in enumerate(sample["source_record"]["食材清单"].split("；"), 1):
        label = raw.split("：", 1)[-1]
        ref = f"ingredient:{rid}:{index:03}"
        evidence.append(provenance(ref, ROOT / sample["source_file"], rid, "/食材清单", raw))
        match = expression.fullmatch(label)
        quantity = dict(quantity_kind="UNKNOWN")
        name = label
        if match:
            name, lower, upper, unit, suffix = match.groups()
            unit = unit_map.get(unit, unit)
            assert unit in units
            value = Fraction(lower)
            quantity = dict(
                quantity_kind="RANGE" if upper else "EXACT",
                quantity={"value": value.numerator, "scale": value.denominator, "unit": unit},
            )
            if upper:
                value = Fraction(upper)
                quantity["upper_quantity"] = {
                    "value": value.numerator,
                    "scale": value.denominator,
                    "unit": unit,
                }
            # Parenthetical alternative weight/count is evidence, not a unit conversion.
            if suffix and not suffix.startswith(("（", "(")):
                name, quantity = label, {"quantity_kind": "UNKNOWN"}
        elif "适量" in label:
            name = label.replace("适量", "")
            quantity = dict(quantity_kind="QUALITATIVE", qualitative_quantity="适量")
        spec_id = f"ingredient_{index:03}"
        specs.append(
            dict(
                spec_id=spec_id,
                ingredient_id=f"{rid}:{spec_id}",
                name=name,
                state="按原文原料描述，半成品前置条件待审",
                provenance_refs=[ref],
                review_status="NEEDS_REVIEW",
            )
        )
        requirements.append(
            dict(
                requirement_id=f"demand_{index:03}",
                spec_id=spec_id,
                provenance_refs=[ref],
                **quantity,
            )
        )
    return specs, requirements


def convert(sample: dict, recipe: dict, dataset: dict) -> tuple[dict, list[dict]]:
    """Copy V3 process content and evidence without inventing an approval."""
    rid = sample["recipe_id"]
    if sample.get("review_records") or sample.get("review_status") == "APPROVED":
        raise ValueError(f"已有人工记录，拒绝覆盖：{rid}")
    if sample["source_record"] != recipe["source_record"]:
        raise ValueError(f"V3原始菜谱记录不一致：{rid}")
    draft = CanonicalRecipeModel.model_validate(recipe["canonical"])
    if draft.recipe_id.root != rid or draft.name != sample["name"]:
        raise ValueError(f"V3菜谱身份不一致：{rid}")
    if draft.review_status != "NEEDS_REVIEW" or draft.approval is not None:
        raise ValueError(f"V3开发草稿不得携带批准：{rid}")
    if any(op.review_status != "NEEDS_REVIEW" for op in draft.operations):
        raise ValueError(f"V3工序必须保持待审：{rid}")

    root_ref = f"scheduling-v3:{rid}"
    revision_evidence = provenance(
        root_ref,
        REVISION,
        rid,
        "/recipes/canonical",
        "V3 AI建议工艺、数值时长及物料流；未经人工批准。",
    )
    revision_evidence["origin"] = "MODEL_SUGGESTION"
    # CSV snippets are original evidence. V3 ingredient records supersede V2
    # records with the same identity, keeping their actual source and hash.
    evidence_by_id = {
        e["provenance_id"]: e
        for e in sample["evidence"]
        if e["provenance_id"].startswith("csv:") or e.get("origin") == "SOURCE_EXPLICIT"
    }
    evidence_by_id.setdefault(
        f"csv:{rid}",
        provenance(
            f"csv:{rid}",
            ROOT / sample["source_file"],
            rid,
            "/",
            json.dumps(sample["source_record"], ensure_ascii=False),
        ),
    )
    for entry in [
        *recipe.get("evidence", []),
        *(e for e in dataset["provenance"] if e["record_id"] == rid),
        revision_evidence,
    ]:
        evidence_by_id[entry["provenance_id"]] = entry

    issue = DataIssue(
        issue_id=f"sample-{rid}-HUMAN_REVIEW",
        recipe_id=rid,
        field_path="/approval",
        code="HUMAN_REVIEW",
        description="V3时长、人工操作、设备替代及物料映射为AI建议；尚无匹配当前工艺哈希的真实人工审核。",
        evidence_refs=(root_ref,),
        required_action="逐项审核AI建议与原文差异，保留真实审核者、依据、日期及工艺内容哈希；不能只修改批准状态。",
    ).model_dump(mode="json")
    issue_ids = list(dict.fromkeys([*draft.issue_refs, issue["issue_id"]]))
    payload = draft.model_dump(mode="json")
    payload["issue_refs"] = issue_ids
    payload["provenance_refs"] = list(
        dict.fromkeys([*payload["provenance_refs"], f"csv:{rid}", root_ref])
    )
    draft = CanonicalRecipeModel.model_validate(payload)
    converted = dict(sample)
    converted.pop("process_map_source", None)
    converted.update(
        knowledge_version=dataset["knowledge_version"],
        review_status="NEEDS_REVIEW",
        review_records=[],
        evidence=list(evidence_by_id.values()),
        canonical_draft=draft.model_dump(mode="json"),
        refinement_source={
            "path": REVISION.relative_to(ROOT).as_posix(),
            "sha256": sha(REVISION),
            "record": recipe["revised_source_record"],
        },
        refinement_detail={
            "path": REVISION.relative_to(ROOT).as_posix(),
            "sha256": sha(REVISION),
        },
        scheduling_context={
            key: recipe.get(key, {} if key in {"source_groups", "operation_metadata"} else [])
            for key in (
                "source_groups",
                "operation_metadata",
                "source_dependencies",
                "resource_reservations",
                "program_constraints",
                "alternative_paths",
                "adaptations",
                "ingredient_supplements",
                "material_policy",
            )
        },
        process_constraints=recipe.get("program_constraints", []),
        unresolved_issue_ids=issue_ids,
        conversion_notes=[
            *recipe.get("review_notes", []),
            *recipe.get("adaptations", []),
            "直接承接V3原子操作、依赖、物料和资源，不再按V2重建空物料草稿。",
            "数值execution_sec为用户授权的AI开发排程建议，保留MODEL_SUGGESTION来源，不代表批准或实测。",
        ],
    )
    return converted, [issue]


def main() -> None:
    manifest, revision = read(MANIFEST), read(REVISION)
    revision_digest = sha(REVISION)
    if revision["review_status"] != "NEEDS_REVIEW" or revision["approved_count"] != 0:
        raise ValueError("P0 AI草稿导入要求V3保持NEEDS_REVIEW且批准数为0")
    for relative, digest in revision["source_hashes"].items():
        if sha(ROOT / relative) != digest:
            raise ValueError(f"来源已变更：{relative}")
    recipes = {r["recipe_id"]: r for r in revision["recipes"]}
    if len(recipes) != 100 or len(revision["recipes"]) != 100:
        raise ValueError("V3必须包含100个互不重复的原始菜谱ID")
    pending, all_issues = [], []
    for entry in manifest["real_samples"]:
        sample = read(ROOT / entry["path"])
        converted, issues = convert(sample, recipes[entry["recipe_id"]], revision)
        pending.append((entry, converted))
        all_issues.extend(issues)
    if len(pending) != 12 or len({entry["recipe_id"] for entry, _ in pending}) != 12:
        raise ValueError("P0必须保留12个不同的原始样本ID")
    if sha(REVISION) != revision_digest:
        raise ValueError("V3数据在导入准备期间已变化，请待重建结束后重试")
    # All source records and canonical models are checked before replacing files.
    for entry, sample in pending:
        write(ROOT / entry["path"], sample)
        entry["sha256"] = sha(ROOT / entry["path"])
        entry["review_status"] = "NEEDS_REVIEW"
    manifest["knowledge_version"] = revision["knowledge_version"]
    manifest.setdefault("historical_source_hashes", dict(manifest["source_hashes"]))
    manifest["source_hashes"] = dict(revision["source_hashes"])
    manifest["provenance_gaps"] = revision.get("provenance_gaps", [])
    manifest["source_hashes"][REVISION.relative_to(ROOT).as_posix()] = revision_digest
    for feature in manifest["coverage"].values():
        feature["status"] = "STRUCTURED_DRAFT_REVIEW_PENDING"
    manifest["review_gate"] = {"required_approved_recipes": 12, "approved_count": 0}
    write(MANIFEST, manifest)
    write(
        ROOT / "data/issues/sample_review.json",
        dict(
            schema_version="1.0",
            status="NEEDS_REVIEW",
            knowledge_version=revision["knowledge_version"],
            refinement_detail={
                "path": REVISION.relative_to(ROOT).as_posix(),
                "sha256": revision_digest,
            },
            issues=all_issues,
            note="V3数值时长、设备兼容替代及物料工艺为AI建议；12道样本仍缺真实人工审核，审核门不通过。",
        ),
    )
    lines = [
        "# P0 十二道样本审核清单",
        "",
        "已接入V3 scheduling_dataset.json；12个原菜谱ID和原始CSV证据保持可追溯。",
        "execution_sec包含AI建议值；NEEDS_REVIEW不因数值齐全而变为APPROVED。",
        "工序、物料、依赖和资源直接来自V3；设备兼容替代与AI补齐项目仍需真实审核。",
        "",
        "| 菜谱 | ID | 操作数 | 依赖数 | 状态 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for _, sample in pending:
        draft = sample["canonical_draft"]
        lines.append(
            f"| {sample['name']} | {sample['recipe_id']} | {len(draft['operations'])} | "
            f"{len(draft['dependencies'])} | NEEDS_REVIEW |"
        )
    lines += [
        "",
        "逐项审核人工时段、热参数、物料投入产出、前置准备、固定分批及套餐中途加料。",
        "记录审核人、依据、日期和匹配工艺内容的哈希；AI建议不能标为人工审核或实测。",
        "来源、V3文件SHA-256、原始CSV片段与AI建议证据保存在各fixture。",
        "当前0个APPROVED；P0-06及整个P0仍未通过人工审核门。",
    ]
    (ROOT / "data/issues/sample_review.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "samples": len(pending),
                "operations": sum(len(s["canonical_draft"]["operations"]) for _, s in pending),
                "dependencies": sum(len(s["canonical_draft"]["dependencies"]) for _, s in pending),
                "issues": len(all_issues),
                "approved": 0,
                "knowledge_version": revision["knowledge_version"],
                "revision_sha256": revision_digest,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
