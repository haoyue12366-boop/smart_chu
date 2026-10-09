"""按用户 2026-09-28 授权修正热水烫洗，生成不可覆盖的待审开发版本。"""

import copy
import hashlib
import json
from pathlib import Path

from app.domain.canonical_recipe import CanonicalRecipeModel
from app.domain.provenance import ArtifactRef, ProvenanceRecord

ROOT = Path(__file__).resolve().parents[1]
BASE = "data/development/development-v3-rebased-v1/dataset.json"
VERSION = "development-v3-rebased-v2"
FISH_ID = "58e70ae1a3fd4a750f4b75b0"
EVIDENCE = "development-correction:fish-hot-water-v1"
AUTHORIZATION = "data/development/authorizations/fish-hot-water-v1.json"


def build_corrected_version(root: Path, output: Path) -> dict:
    raw = (root / BASE).read_bytes()
    data = json.loads(raw)
    if data["knowledge_version"] != "development-v3-rebased-v1":
        raise ValueError("修正必须基于指定的不可变 V1")
    authorization = (root / AUTHORIZATION).read_bytes()
    auth_ref = ArtifactRef(
        path=AUTHORIZATION,
        sha256=hashlib.sha256(authorization).hexdigest(),
        media_type="application/json",
    )
    row = next(r for r in data["recipes"] if r["recipe_id"] == FISH_ID)
    old_row = copy.deepcopy(row)
    canonical = row["canonical"]
    ops = {o["operation_id"]: o for o in canonical["operations"]}
    wash, removed_unload = ops["op_002_02"], ops["op_8002_01"]
    if (
        wash["duration"]["execution_sec"] != 15
        or ops["op_1001_01"]["duration"]["execution_sec"] != 300
    ):
        raise ValueError("既有原文时间或热水估时变化，需重新核对修正")
    evidence = ProvenanceRecord(
        provenance_id=EVIDENCE,
        origin="SOURCE_DERIVED",
        source_file=AUTHORIZATION,
        source_hash=auth_ref.sha256,
        record_id=FISH_ID,
        field_path="/correction",
        text_span="用户同意保留既有热水准备和 15 秒人工烫洗，移除错误蒸箱步骤；仍待正式审核。",
        artifact_ref=auth_ref,
    ).model_dump(mode="json")
    row["evidence"].append(evidence)
    canonical["provenance_refs"].append(EVIDENCE)
    human = copy.deepcopy(ops["op_001_01"]["resource_requirements"][0])
    human["evidence_refs"] = [EVIDENCE]
    wash.update(
        action="WASH",
        description="人工用约90℃已备热水烫洗鱼头15秒。",
        resource_requirements=[human],
        material_outputs=removed_unload["material_outputs"],
    )
    wash["provenance_refs"].append(EVIDENCE)
    wash["duration"]["source_ref"] = EVIDENCE
    for demand in wash["material_outputs"]:
        demand["provenance_refs"].append(EVIDENCE)
    removed = {"op_002_01", "op_8002_01"}
    canonical["operations"] = [
        o for o in canonical["operations"] if o["operation_id"] not in removed
    ]
    dependencies = []
    seen = set()
    for dep in canonical["dependencies"]:
        before = dep["predecessor_id"]
        after = dep["successor_id"]
        dep["predecessor_id"] = "op_002_02" if before in removed else before
        dep["successor_id"] = "op_002_02" if after in removed else after
        pair = dep["predecessor_id"], dep["successor_id"]
        if pair[0] == pair[1] or pair in seen:
            continue
        if before in removed or after in removed:
            dep["evidence_refs"].append(EVIDENCE)
            dep["reason"] = "热水准备和去鳃先于人工烫洗，烫洗后刮鳞清洗"
        dependencies.append(dep)
        seen.add(pair)
    canonical["dependencies"] = dependencies
    canonical["material_specs"] = [
        s for s in canonical["material_specs"] if s["spec_id"] != "flow_002_00_00"
    ]
    row["source_groups"]["2"] = ["op_002_02"]
    row["resource_reservations"] = [
        r for r in row["resource_reservations"] if not removed.intersection(r["members"])
    ]
    for oid in removed:
        row["operation_metadata"].pop(oid)
    metadata = row["operation_metadata"]["op_002_02"]
    metadata.update(
        material_output_specs=["scalded_fish"],
        output_state="约90℃热水烫15秒",
        predecessor_states=["鱼头去鳃", "约90℃热水已制备并移出灶具"],
        basis="原文为15秒人工热水烫洗；用户授权去除错误蒸箱映射。",
    )
    row["operation_metadata"]["op_003_01"]["predecessor_states"] = ["约90℃热水烫15秒"]
    row["material_flow"]["producers"].pop("flow_002_00_00")
    row["material_flow"]["consumed_fractions"].pop("flow_002_00_00")
    row["material_flow"]["producers"]["scalded_fish"] = "op_002_02"
    row["review_notes"].append(
        "用户授权热水烫洗修正；既有热水300秒及移出60秒仍为AI估计，未获正式审核。"
    )
    CanonicalRecipeModel.model_validate(canonical)
    data["knowledge_version"] = VERSION
    data["source_hashes"][BASE] = hashlib.sha256(raw).hexdigest()
    data["source_hashes"][AUTHORIZATION] = auth_ref.sha256
    report = {
        "knowledge_version": VERSION,
        "base_sha256": hashlib.sha256(raw).hexdigest(),
        "recipe_id": FISH_ID,
        "authorization": auth_ref.model_dump(mode="json"),
        "removed_operation_ids": sorted(removed),
        "review_status": "NEEDS_REVIEW",
        "approved_count": 0,
        "formal_release_eligible": False,
        "previous_recipe": old_row,
        "corrected_recipe": row,
    }
    output.mkdir(parents=True, exist_ok=True)
    for name, value in (("dataset.json", data), ("correction.json", report)):
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        path = output / name
        if path.exists() and path.read_text("utf-8") != payload:
            raise ValueError("不可覆盖已存在开发版本；必须创建新版本")
        path.write_text(payload, encoding="utf-8")
    return report


if __name__ == "__main__":
    build_corrected_version(ROOT, ROOT / "data/development" / VERSION)
    print(f"{VERSION}: 100 recipes, 1562 operations, NEEDS_REVIEW")
