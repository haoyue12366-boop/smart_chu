"""从现存 V3 创建新的待审开发基线；保留历史断链，不重造丢失文件。"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from app.domain.canonical_recipe import CanonicalRecipeModel
from app.domain.provenance import ArtifactRef
from app.pipeline.import_raw import import_recipes

ROOT = Path(__file__).resolve().parents[1]
VERSION = "development-v3-rebased-v1"
DATASET = "data/revisions/recipes_v3/scheduling_dataset.json"
CSV = "data/revisions/recipes_v3/recipes_100_调度版.csv"
ORIGINAL = "docs/recipes_100.csv"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_development_version(root: Path, output: Path) -> dict:
    data = json.loads((root / DATASET).read_text(encoding="utf-8"))
    csv = import_recipes(root / CSV)
    original = import_recipes(root / ORIGINAL)
    identities = [(s.recipe_id.root, s.name) for s in original]
    if len(identities) != 100 or len(set(rid for rid, _ in identities)) != 100:
        raise ValueError("原始固定 100 ID 不完整")
    if [(s.recipe_id.root, s.name) for s in csv] != identities or [
        (r["recipe_id"], r["name"]) for r in data["recipes"]
    ] != identities:
        raise ValueError("原文、V3 CSV 和现存 JSON 身份不一致")
    if data["review_status"] != "NEEDS_REVIEW" or data["approved_count"] != 0:
        raise ValueError("开发重建不能携带人工批准")
    for row, source in zip(data["recipes"], csv, strict=True):
        recipe = CanonicalRecipeModel.model_validate(row["canonical"])
        if recipe.approval is not None or recipe.review_status != "NEEDS_REVIEW":
            raise ValueError("开发菜谱不能携带批准")
        if (recipe.recipe_id.root, recipe.name) != (row["recipe_id"], row["name"]):
            raise ValueError("规范化菜谱身份不一致")
        by_id = {op.operation_id.root: op for op in recipe.operations}
        found = set()
        for step in source.steps:
            match = re.search(r"id=([^｜]+)｜时间=(\d+)秒", step.text)
            if match is None or match[1] not in by_id or match[1] in found:
                raise ValueError("V3 CSV 操作身份缺失或重复")
            operation = by_id[match[1]]
            if (
                operation.duration.execution_sec != int(match[2])
                or re.sub(r"第(\d+)步", r"原始步骤\1", operation.description) not in step.text
                or operation.review_status != "NEEDS_REVIEW"
            ):
                raise ValueError("V3 CSV 与 JSON 操作内容不一致")
            found.add(match[1])
        if found != set(by_id):
            raise ValueError("V3 CSV 操作覆盖不完整")

    historical = dict(data["source_hashes"])
    claims: dict[tuple[str, str], str] = {
        (p, sha): "/source_hashes" for p, sha in historical.items()
    }

    def collect(value, location=""):
        if isinstance(value, dict):
            if "source_file" in value and "source_hash" in value:
                claims[(value["source_file"], value["source_hash"])] = location
            if "artifact_ref" in value and value["artifact_ref"]:
                ref = ArtifactRef.model_validate(value["artifact_ref"])
                claims[(ref.path, ref.sha256)] = location
            for key, child in value.items():
                collect(child, location + "/" + key)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                collect(child, location + f"/{index}")

    collect(data)
    present = {p: digest(root / p) for p in (DATASET, CSV, ORIGINAL)}
    gaps = []
    broken = set()
    for (relative, expected), locator in sorted(claims.items()):
        ref = ArtifactRef(path=relative, sha256=expected, media_type="application/octet-stream")
        path = root / ref.path
        actual = digest(path) if path.is_file() else None
        if actual == expected:
            ref.verify(root)
            present[relative] = actual
        else:
            broken.add((relative, expected))
            gaps.append(
                {
                    "path": relative,
                    "expected_sha256": expected,
                    "current_sha256": actual,
                    "locator": locator,
                    "status": "MISSING" if actual is None else "HASH_MISMATCH",
                }
            )
    if not (root / "data/revisions/recipes_v2/corrections.json").is_file():
        gaps.append(
            {
                "path": "data/revisions/recipes_v2/corrections.json",
                "expected_sha256": None,
                "current_sha256": None,
                "locator": "legacy-rebuild-input",
                "status": "MISSING",
            }
        )

    # 新版本的引用证明内容来自现存派生物；历史记录完整保留，不能冒充原始估时归档。
    old_provenance = []

    def reanchor(value, location=""):
        if isinstance(value, dict):
            claim = (value.get("source_file"), value.get("source_hash"))
            if "provenance_id" in value and claim in broken:
                old_provenance.append({"locator": location, "record": dict(value)})
                value.update(
                    source_file=DATASET,
                    source_hash=present[DATASET],
                    field_path=location,
                    origin="MODEL_SUGGESTION",
                    text_span="现存 V3 派生记录；历史来源有缺口，待人工核对。",
                    review_status="NEEDS_REVIEW",
                    approved_review_ref=None,
                    artifact_ref={
                        "path": DATASET,
                        "sha256": present[DATASET],
                        "media_type": "application/json",
                    },
                )
            for key, child in list(value.items()):
                reanchor(child, location + "/" + key)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                reanchor(child, location + f"/{index}")

    reanchor(data)
    data.update(
        knowledge_version=VERSION,
        release_kind="development",
        source_hashes=present,
        historical_source_hashes=historical,
        historical_provenance=old_provenance,
        provenance_gaps=gaps,
    )
    report = {
        "schema_version": "1.0",
        "knowledge_version": VERSION,
        "release_kind": "development",
        "review_status": "NEEDS_REVIEW",
        "approved_count": 0,
        "formal_release_eligible": False,
        "recipe_count": len(data["recipes"]),
        "operation_count": sum(len(r["canonical"]["operations"]) for r in data["recipes"]),
        "source_hashes": present,
        "provenance_gaps": gaps,
        "reanchored_provenance_count": len(old_provenance),
        "note": "用户授权以现存 V3 建立开发版本；不恢复或伪造历史源文件，不表示人工审核。",
    }
    output.mkdir(parents=True, exist_ok=True)
    for name, content in (("dataset.json", data), ("provenance_gaps.json", report)):
        encoded = json.dumps(content, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        path = output / name
        if path.exists() and path.read_text("utf-8") != encoded:
            raise ValueError("开发版本内容变化，必须选择新版本目录")
        path.write_text(encoded, encoding="utf-8")
    return report


if __name__ == "__main__":
    report = build_development_version(ROOT, ROOT / "data/development" / VERSION)
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "knowledge_version",
                    "recipe_count",
                    "operation_count",
                    "approved_count",
                )
            },
            ensure_ascii=False,
        )
    )
