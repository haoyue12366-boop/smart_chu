"""根据原文整理待审开发样本，不估造时长、不生成审核批准。"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from pathlib import Path

from app.domain.canonical_recipe import CanonicalRecipeModel
from app.pipeline.issues import DataIssue
from tests.helpers import recipe_payload

ROOT = Path(__file__).resolve().parents[1]
NAMES = {
    "鸡翅包饭",
    "鸡仔饼",
    "家常鲈鱼",
    "广式叉烧",
    "糯米烧麦",
    "亲朋欢聚套餐",
    "土豆焖饭",
    "香菇烤芦笋",
    "麻辣对虾",
    "烹香酷炒汇",
    "蒜蓉粉丝蒸扇贝",
}
FEATURES = {
    "long_preparation": ["鸡仔饼", "广式叉烧", "糯米烧麦"],
    "recipe_parallelism": ["香菇烤芦笋"],
    "two_burner_contention": ["家常鲈鱼", "土豆焖饭", "鸡翅包饭"],
    "device_relay": ["鸡翅包饭", "家常鲈鱼", "土豆焖饭"],
    "different_heat_durations": ["家常鲈鱼", "土豆焖饭", "鸡翅包饭"],
    "explicit_batches": ["糯米烧麦"],
    "intervention": ["亲朋欢聚套餐", "烹香酷炒汇", "香菇烤芦笋"],
    "duplicate_names": ["麻辣对虾"],
    "shared_cutting_candidate": ["麻辣对虾", "蒜蓉粉丝蒸扇贝"],
}
SPECIAL = {
    "鸡翅包饭": (
        "第3步腌制、4–5步炒饭与人工装填缺时长；常规烘焙与清单模式须审核映射；保留先炒后烤。"
    ),
    "鸡仔饼": "第1步冷冻至少24小时，不可删减；第5步屏幕程序缺温度、模式、时长；冷冻温区设置待审。",
    "家常鲈鱼": "第5步明确普通蒸100℃8分钟；取鱼、倒汁、热油及浇汁人工时长与灶具档位待审。",
    "广式叉烧": "第3步至少冷藏24小时；第4步回温时长未知；第5步屏幕烹饪参数缺失。",
    "糯米烧麦": (
        "保留4小时浸泡、100℃40分钟蒸米和40个分两批的强制"
        "工艺；最后两批蒸制参数缺失；冷却、肉料熟制与汤匙单位待审"
        "。"
    ),
    "亲朋欢聚套餐": (
        "鸡的前置腌制没有给出；排骨冰箱腌制2小时；剩15分钟中途"
        "加入必须保留；总程序、腔体映射及分段参数缺失；大米200"
        "g未列在食材清单。"
    ),
    "土豆焖饭": (
        "保留100℃20分钟和25分钟两段蒸制及中间翻炒；清单水"
        "180缺单位而步骤为180g，须确定采用依据；锅内操作时"
        "长与档位待审。"
    ),
    "香菇烤芦笋": (
        "保留190℃8分钟烤芦笋与同时煎香菇的并行分支；完全烧烤模式映射、翻面窗口和人工时长待审。"
    ),
    "麻辣对虾": (
        "两条ID分别保留；腌制10分钟明确；屏幕程序及冷却时长缺"
        "失；不同原料单位不自动折算；蒜末共享需全组工艺与数量依据"
        "。"
    ),
    "烹香酷炒汇": (
        "第17步预热、取出、翻拌、加盖锡纸和回炉均须拆分；总程序"
        "和介入时间缺失；同腔体固定套餐不能冒充自由跨菜合批。"
    ),
    "蒜蓉粉丝蒸扇贝": (
        "粉丝约10分钟泡软；扇贝清理、油蒜末和装填人工时长待审；"
        "屏幕蒸制参数缺失；蒜末50g与油蒜末分配须核对。"
    ),
}


def write_json(relative: str, data: object) -> str:
    path = ROOT / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    source = ROOT / "docs/recipes_100.csv"
    rows = list(csv.DictReader(source.read_text(encoding="utf-8-sig").splitlines()))
    assert len(rows) == len({r["菜谱id"] for r in rows}) == 100
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    # Preflight every selected fixture before any write, including recipes that
    # occur later in the CSV. A legacy extraction must never downgrade V2/V3.
    for row in rows:
        if row["名称"] not in NAMES:
            continue
        path = ROOT / f"tests/fixtures/reviewed_sample/{row['菜谱id']}.json"
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if (
                existing.get("knowledge_version", "sample-p0-draft-v1") != "sample-p0-draft-v1"
                or existing.get("review_records")
                or existing.get("review_status") == "APPROVED"
            ):
                raise ValueError(f"已有修订版或人工审核记录，拒绝覆盖：{path.name}")
    entries, issues, checklist = [], [], []
    for line, row in enumerate(rows, start=2):
        name, rid = row["名称"], row["菜谱id"]
        if name not in NAMES:
            continue
        steps = list(
            re.finditer(r"第(\d+)步[：:](.*?)(?=第\d+步[：:]|$)", row["烹饪步骤"], flags=re.S)
        )
        operations, evidence = [], []
        for match in steps:
            step, raw = int(match.group(1)), match.group(2)
            ref = f"csv:{rid}:step:{step}"
            evidence.append(
                {
                    "provenance_id": ref,
                    "source_file": "docs/recipes_100.csv",
                    "source_hash": source_hash,
                    "record_id": rid,
                    "csv_line": line,
                    "step_number": step,
                    "text_span": raw,
                    "origin": "SOURCE_EXPLICIT",
                    "duration_mentions": re.findall(
                        r"\d+(?:\.\d+)?(?:分钟|min|小时)(?:以上)?", raw
                    ),
                }
            )
            operations.append(
                {
                    "operation_id": f"raw_step_{step:02d}",
                    "action": "UNKNOWN",
                    "description": raw,
                    "duration": {"source_ref": ref},
                    "provenance_refs": [ref],
                    "review_status": "NEEDS_REVIEW",
                }
            )
        issue_id = f"sample-{rid}-path-review"
        issue = DataIssue(
            issue_id=issue_id,
            recipe_id=rid,
            field_path="/operations",
            code="UNREVIEWED_EXECUTION_PATH",
            description=SPECIAL[name],
            evidence_refs=tuple(e["provenance_id"] for e in evidence),
            required_action="审核原子拆分、每段人工/等待/设备时长、输入输出量、设备物理映射、模式参数与依赖；给出审核者、依据、版本及日期。",
        )
        issues.append(issue.model_dump(mode="json"))
        draft = CanonicalRecipeModel.model_validate(
            {
                "schema_version": "1.0",
                "recipe_id": rid,
                "name": name,
                "recipe_version": "p0-draft-1",
                "ingredient_requirements": [],
                "material_specs": [],
                "operations": operations,
                "dependencies": [],
                "review_status": "NEEDS_REVIEW",
                "provenance_refs": [f"csv:{rid}"],
                "issue_refs": [issue_id],
            }
        )
        relative = f"tests/fixtures/reviewed_sample/{rid}.json"
        # 重新运行不能覆盖用户随后补充的人工记录。
        path = ROOT / relative
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing.get("review_records") or existing.get("review_status") == "APPROVED":
                raise ValueError(f"已有审核记录，拒绝覆盖：{relative}")
        sample = {
            "source_kind": "REAL_SOURCE",
            "recipe_id": rid,
            "name": name,
            "source_file": "docs/recipes_100.csv",
            "source_hash": source_hash,
            "csv_line": line,
            "source_record": row,
            "evidence": evidence,
            "knowledge_version": "sample-p0-draft-v1",
            "review_status": "NEEDS_REVIEW",
            "review_records": [],
            "unresolved_issue_ids": [issue_id],
            "canonical_draft": draft.model_dump(mode="json"),
        }
        digest = write_json(relative, sample)
        entries.append(
            {
                "recipe_id": rid,
                "name": name,
                "path": relative,
                "sha256": digest,
                "review_status": "NEEDS_REVIEW",
                "source_kind": "REAL_SOURCE",
            }
        )
        checklist.append(f"| {name} | {rid} | CSV第{line}行 | {SPECIAL[name]} |")
    synthetic = [
        (
            "canonical_complex",
            recipe_payload(),
            "结构合法",
            "合成并行、固定两批和精确中途介入，可无损序列化",
        ),
        (
            "second_human",
            {"resource_type": "HUMAN", "resource_id": "human_2", "conflict_policy": "UNARY"},
            "拒绝",
            "仅允许 human_1",
        ),
        (
            "unmapped_device",
            {"resource_type": "DEVICE", "resource_id": "oven_1", "review_status": "APPROVED"},
            "拒绝",
            "缺少物理映射及竞争策略",
        ),
    ]
    synthetic_entries = []
    for name, data, expected, reason in synthetic:
        relative = f"tests/fixtures/synthetic/{name}.json"
        digest = write_json(relative, data)
        synthetic_entries.append(
            {
                "path": relative,
                "sha256": digest,
                "source_kind": "SYNTHETIC",
                "expected_result": expected,
                "expected_reason": reason,
            }
        )
    write_json(
        "tests/fixtures/fixture_manifest.json",
        {
            "schema_version": "1.0",
            "knowledge_version": "sample-p0-draft-v1",
            "review_status": "NEEDS_REVIEW",
            "source_hashes": {"docs/recipes_100.csv": source_hash},
            "real_samples": entries,
            "synthetic_samples": synthetic_entries,
            "coverage": {
                key: {
                    "recipe_ids": [e["recipe_id"] for e in entries if e["name"] in names],
                    "status": "SOURCE_IDENTIFIED_REVIEW_PENDING",
                }
                for key, names in FEATURES.items()
            },
            "review_gate": {"required_approved_recipes": 12, "approved_count": 0},
        },
    )
    write_json(
        "data/issues/sample_review.json",
        {
            "schema_version": "1.0",
            "status": "NEEDS_REVIEW",
            "issues": issues,
            "note": "原编号步骤仅为待审原文容器，不是已确认的原子调度路径。",
        },
    )
    text = (
        "# P0 样本待审核清单\n\n"
        "12 个真实菜谱 ID，均为 NEEDS_REVIEW，当前 0 个已批准。"
        "原文片段、CSV行号和SHA-256已保存；没有填入AI估计的时长或伪造审核者。\n\n"
        "每道菜都需要确认：原子操作、主动人工/被动等待、时长、物料投入产出、"
        "依赖关系、物理设备及配置、预热/装卸/介入。已明确的数值也须连同完整路径审核。"
        "相同食材仅是共享候选线索，并不证明可共享。\n\n"
        "| 菜谱 | ID | 来源 | 需要确认的具体事项 |\n| --- | --- | --- | --- |\n"
        + "\n".join(checklist)
        + "\n\n请逐字段记录采用值、单位、依据、审核者、审核日期及菜谱版本。"
        "修改使用 ReviewPatch 保留前后值；批准必须匹配当前工艺内容哈希。"
        "补齐原子路径、物料、设备与依赖后再审核，不能只把状态字段改为 APPROVED。\n"
    )
    (ROOT / "data/issues/sample_review.md").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
