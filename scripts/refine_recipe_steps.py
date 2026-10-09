"""Build the ID-preserving recipe revision as review data, not a knowledge release."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/revisions/recipes_v2"
SOURCE = ROOT / "docs/recipes_100_详细步骤.csv"
ORIGINAL = ROOT / "docs/recipes_100.csv"
STEP = re.compile(r"第(\d+)步【([^】]*)】(.*?)(?=第\d+步【|$)", re.S)
TIME = re.compile(
    r"(?:约|至少)?\s*(?:\d+(?:\.\d+)?(?:\s*[~～－–—-]\s*\d+(?:\.\d+)?)?"
    r"\s*(?:个)?\s*(?:分钟|小时|秒|min|h)|半(?:个)?小时|[一二两三四五六七八九十]+小时)"
    r"(?:以上|左右)?",
    re.I,
)
HEAT = re.compile(
    r"煮沸|煮至|煮熟(?!的)|蒸至|煎至|煸至|煸炒|翻炒|炒香(?!(?:的|后))|烧热|烧开|加热|"
    r"锅置火|下锅|入油锅|热油|煮开|炒制|煎熟|烧至|炒成|煸香|锅中.{0,20}小火"
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def dump(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_steps(text: str) -> list[dict]:
    matches = list(STEP.finditer(text))
    assert "".join(m[0] for m in matches) == text, "Step parser lost source text"
    assert [int(m[1]) for m in matches] == list(range(1, len(matches) + 1))
    result = []
    for m in matches:
        meta = m[2]
        human = re.search(r"人工：厨师1人，([^；]+)", meta)
        resources = re.search(r"设备(?:/资源)?：([^；]+)", meta)
        duration = re.search(r"(?:等待/资源占用|设备占用)：([^；]+)", meta)
        result.append(
            dict(
                source_steps=[int(m[1])],
                source_annotations=[meta],
                source_bodies=[m[3]],
                text=m[3],
                kind={"人工操作": "人工", "收尾": "人工", "烹饪": "热加工", "等待/被动": "等待"}[
                    meta.split("｜")[0]
                ],
                resource=resources[1] if resources else "待确认",
                prior_human_suggestion=human[1] if human else None,
                prior_duration_suggestion=duration[1] if duration else None,
                time="",
                note="",
                changes=[],
                patched=False,
            )
        )
    return result


def apply_patches(recipe_id: str, steps: list[dict], config: dict) -> list[dict]:
    patches = [p for p in config["patches"] if p["recipe_id"] == recipe_id]
    covered: set[int] = set()
    replacements = {}
    for patch in patches:
        start, end = patch["start"], patch["end"]
        assert 1 <= start <= len(steps) + 1 and end <= len(steps)
        indices = set(range(start, end + 1))
        assert not covered.intersection(indices), "Overlapping corrections"
        covered.update(indices)
        originals = steps[start - 1 : end]
        items = []
        for item in patch["items"]:
            items.append(
                dict(
                    **item,
                    source_steps=sorted(indices),
                    source_annotations=[s["source_annotations"][0] for s in originals],
                    source_bodies=[s["text"] for s in originals],
                    prior_human_suggestion=None,
                    prior_duration_suggestion=None,
                    patched=True,
                    changes=[patch["reason"]],
                )
            )
        assert start not in replacements
        replacements[start] = (end, items)
    result = []
    number = 1
    while number <= len(steps) + 1:
        if number in replacements:
            end, items = replacements[number]
            result.extend(items)
            number = max(number + 1, end + 1)
        elif number <= len(steps):
            s = steps[number - 1]
            override = config["overrides"].get(f"{recipe_id}:{number}", {})
            if override:
                s.update(override)
                s["changes"].append("按正文校正分类、资源或时间")
                if override.get("discard_estimate"):
                    s["prior_human_suggestion"] = None
                    s["prior_duration_suggestion"] = None
            result.append(s)
            number += 1
        else:
            break
    return result


def normalize_resource(s: dict, recipe_id: str, config: dict) -> None:
    text, resource = s["text"], s["resource"]
    previous = resource
    if "蒸箱/蒸烤一体机" in resource:
        if "烤箱" in text and "蒸烤箱" not in text:
            resource = resource.replace("蒸箱/蒸烤一体机", "烤制设备")
        elif recipe_id in config["oven_recipe_ids"] and re.search("屏|开始烹饪", text):
            resource = resource.replace("蒸箱/蒸烤一体机", "烤制设备（程序能力待核实）")
        else:
            resource = resource.replace("蒸箱/蒸烤一体机", "蒸制/蒸烤设备（模式及腔体待确认）")
    resource = resource.replace("烤箱/蒸烤一体机", "烤制设备")
    if HEAT.search(text) and s["kind"] == "人工":
        s["kind"] = "复合工序"
        s["changes"].append("正文含供热操作，取消纯人工分类")
        s["note"] += "；含供热和人工操作，需分别确定持续时间"
    if HEAT.search(text) and not re.search("灶具|蒸|烤|水浴|热水", resource):
        resource = "灶具+锅具；" + resource
    if s["kind"] == "热加工" and HEAT.search(text) and resource == "基础厨具/操作台":
        resource = "灶具+锅具"
    if re.search("放冰箱|放入冰箱|从冰箱|取出冰箱|进冰箱", text) and "冰箱" not in resource:
        resource += "+冰箱（温区由相邻存储工序确定）"
    if s["kind"] == "等待" and resource == "基础厨具/操作台":
        resource = "承载容器/存放区（条件待确认）"
    if re.search("打开.*?箱门|开箱门", text) and "箱" not in resource:
        resource += "+烹饪设备（承接前一热操作）"
    s["resource"] = resource
    if resource != previous:
        s["changes"].append("校正/澄清设备能力或跨步占用")


def time_constraint(text: str) -> dict:
    result = dict(text=text or "待补", relation="unknown", lower_sec=None, upper_sec=None)
    if not text:
        return result
    cleaned = re.sub(r"\s|个", "", text)
    for chinese, numeric in {
        "半小时": "0.5小时",
        "一小时": "1小时",
        "两小时": "2小时",
        "二小时": "2小时",
    }.items():
        cleaned = cleaned.replace(chinese, numeric)
    match = re.fullmatch(
        r"(约|至少)?(\d+(?:\.\d+)?)(?:[~～－–—-](\d+(?:\.\d+)?))?"
        r"(分钟|小时|秒|min|h)(以上|左右)?",
        cleaned,
        re.I,
    )
    if not match:
        return result
    factor = {"分钟": 60, "小时": 3600, "秒": 1, "min": 60, "h": 3600}[match[4].lower()]
    lower, upper = float(match[2]) * factor, float(match[3] or match[2]) * factor
    assert lower.is_integer() and upper.is_integer(), "Non-integer seconds require review"
    result.update(lower_sec=int(lower), upper_sec=int(upper))
    if match[1] == "至少" or match[5] == "以上":
        result.update(relation="minimum", upper_sec=None)
    elif match[3]:
        result["relation"] = "range"
    elif match[1] == "约" or match[5] == "左右":
        result.update(
            relation="approximate", lower_sec=None, upper_sec=None, nominal_sec=int(lower)
        )
    else:
        result["relation"] = "exact_source"
    return result


def normalize(s: dict, recipe_id: str, config: dict) -> dict:
    text = s["text"]
    if re.fullmatch(r"(食材准备|准备食材|制作碳烧汁)[。！!]*", text):
        s.update(kind="说明", time="", prior_human_suggestion=None, prior_duration_suggestion=None)
        s["note"] += "；标题不单独累计2分钟，具体动作见后续"
        s["changes"].append("标题不作为额外工序")
    if re.search(r"可以吸的鸡蛋羹|在寒冷的冬天为家人", text):
        s.update(kind="说明", prior_human_suggestion=None, prior_duration_suggestion=None)
        s["changes"].append("宣传/品尝文字不作为工序")
    normalize_resource(s, recipe_id, config)
    if not s["time"] and s["kind"] not in {"中途介入", "说明"} and not re.search("剩余|还剩", text):
        times = [m[0].strip() for m in TIME.finditer(text)]
        if len(times) == 1:
            s["time"] = times[0]
        elif len(times) > 1:
            s["time"] = "原文分段：" + "、".join(times)
            s["note"] += "；各段计时需按动作定位，不作为整步总时长"
    s["source_time_constraint"] = time_constraint(s["time"])
    s["execution_duration_sec"] = None
    s["review_status"] = "NEEDS_REVIEW"
    s["human_resource_id"] = None if s["kind"] == "说明" else "human_1"
    s["physical_resource_id"] = None
    s["component_id"] = None
    issues = []
    if s["kind"] != "说明":
        issues.append("人工时段/估时及物理资源映射待审核")
    if s["kind"] in {"热加工", "等待", "复合工序", "条件分支", "可选等待"} and not s["time"]:
        issues.append("过程持续时间/状态终点待补")
    if re.search("屏幕|屏端|按照提示", text) or ("程序" in text and s["kind"] == "热加工"):
        issues.append("核实设备程序的模式、温度、时长和触发事件")
    if re.search(r"[89]\s*[档段挡]", text):
        issues.append("原文8/9档段火与设备清单1–7冲突，保留原值待核实")
    if re.search(r"240\s*[℃度]", text):
        issues.append("240℃超出设备清单烤制最高230℃，不可静默裁剪")
    if "微波" in s["resource"]:
        issues.append("设备清单未列微波能力；可选路径不得误列必需")
    if "发酵" in text and re.search("35|40|烤箱", text):
        issues.append("当前烤箱清单未列原文低温发酵能力")
    for mode in ["常规烘焙", "完全烧烤", "鼓风烧烤", "分控烘焙"]:
        if mode in text:
            issues.append(f"原文{mode}与设备清单模式的对应关系待审")
    if "分控烘焙" in text:
        issues.append("上下火独立温控能力待确认")
    if "探针" in text:
        issues.append("探针能力和腔体/中心温度终点待核实")
    if "预热好的" in text or "预热的烤箱" in text:
        issues.append("必须先完成预热；预热目标及持续时间待补")
    s["issues"] = list(dict.fromkeys(issues))
    s["note"] = s["note"].strip("；")
    return s


def display(s: dict, number: int) -> str:
    kind = s["kind"]
    if kind == "说明":
        meta = "说明｜不计独立工时"
    else:
        if kind in {"等待", "可选等待"}:
            human = "等待本身不占厨师；进出/检查需human_1另计"
        elif kind == "人工" and s["prior_human_suggestion"]:
            human = "human_1；" + s["prior_human_suggestion"] + "（旧稿估时，待审）"
        else:
            human = "human_1执行操作/介入；人工时段及用时待审"
        if s["time"]:
            duration = s["time"] + "（原文或原文换算；非已批准执行值）"
        else:
            duration = "待补"
            prior = s.get("prior_duration_suggestion")
            if prior and kind in {"等待", "热加工", "复合工序"}:
                match = re.search(r"约\d+(?:\.\d+)?分钟", prior)
                if match:
                    duration += "；旧稿建议" + match[0] + "（待审核，不能直接采用）"
        meta = f"{kind}｜人工：{human}；资源：{s['resource']}；过程时间：{duration}"
    notes = [s["note"]] if s["note"] else []
    notes.extend(i for i in s["issues"] if i != "人工时段/估时及物理资源映射待审核")
    if notes:
        meta += "；核对：" + "；".join(dict.fromkeys(notes))
    return f"第{number}步【{meta}】{s['text']}"


def main() -> None:
    config = json.loads((OUT / "corrections.json").read_text(encoding="utf-8"))
    assert digest(SOURCE) == config["source_sha256"], "Revised source changed; rebase corrections"
    assert digest(ORIGINAL) == config["original_sha256"], "Original source changed"
    revised, original = read_rows(SOURCE), read_rows(ORIGINAL)
    ids = [r["菜谱id"] for r in revised]
    assert len(ids) == len(set(ids)) == 100
    assert ids == [r["菜谱id"] for r in original]
    rows, recipes, changes = [], [], []
    for row_index, row in enumerate(revised):
        rid = row["菜谱id"]
        assert all(row[k] == original[row_index][k] for k in ("菜谱id", "名称", "食材清单"))
        source_steps = parse_steps(row["烹饪步骤"])
        refined = apply_patches(rid, source_steps, config)
        for note in config["recipe_notes"].get(rid, []):
            refined.append(
                dict(
                    kind="说明",
                    text=note,
                    resource="",
                    time="",
                    note="",
                    source_steps=[],
                    source_annotations=[],
                    source_bodies=[],
                    prior_human_suggestion=None,
                    prior_duration_suggestion=None,
                    patched=True,
                    changes=["补充菜谱层面的待核对说明"],
                )
            )
        normalized = [normalize(s, rid, config) for s in refined]
        for number, s in enumerate(normalized, 1):
            s["display_step"] = number
            if s["changes"]:
                changes.append(
                    dict(
                        recipe_id=rid,
                        name=row["名称"],
                        step=number,
                        source_steps=s["source_steps"],
                        changes=s["changes"],
                    )
                )
        output = dict(row)
        output["烹饪步骤"] = "".join(display(s, n) for n, s in enumerate(normalized, 1))
        rows.append(output)
        recipes.append(
            dict(
                recipe_id=rid,
                name=row["名称"],
                csv_row=row_index + 2,
                review_status="NEEDS_REVIEW",
                original_record=original[row_index],
                revised_input_record=row,
                steps=normalized,
                notes=config["recipe_notes"].get(rid, []),
            )
        )
    assert [r["菜谱id"] for r in rows] == ids
    stage = dict(
        version="recipes-v2-review-draft-2026-09-23",
        status="NEEDS_REVIEW",
        scope="文字工艺完善草稿，不是已批准CanonicalRecipeModel或比赛发布包",
        source_hashes={str(p.relative_to(ROOT)): digest(p) for p in (SOURCE, ORIGINAL)},
        headers=list(revised[0]),
        rows=rows,
        recipes=recipes,
    )
    dump(OUT / "recipes_100_完善版.json", stage)
    dump(OUT / "change_log.json", changes)
    pending_lines = [
        "# 完善版剩余审核项",
        "",
        "所有100道仍为NEEDS_REVIEW。每道均需审核人工时段、物料流与物理资源映射。",
        "下列按菜谱ID定位新增的具体事项；估时原作者/依据未提供，不自动认定人工或模型来源。",
        "编号用于阅读；菜内并行、可选分支、中途介入不能按编号一律串行执行。",
        "",
    ]
    for recipe in recipes:
        pending_lines += [f"## {recipe['name']}（{recipe['recipe_id']}）", ""]
        for note in recipe["notes"]:
            pending_lines.append("- " + note)
        for step in recipe["steps"]:
            issues = [i for i in step["issues"] if i != "人工时段/估时及物理资源映射待审核"]
            if step["note"]:
                issues.append(step["note"])
            if issues:
                pending_lines.append(f"- 第{step['display_step']}步：" + "；".join(issues))
        pending_lines.append("")
    (OUT / "待确认事项.md").write_text("\n".join(pending_lines) + "\n", encoding="utf-8")
    stats = dict(
        recipe_count=100,
        unique_ids=100,
        duplicate_name_counts={k: v for k, v in Counter(r["名称"] for r in rows).items() if v > 1},
        revised_steps=sum(len(parse_steps(r["烹饪步骤"])) for r in revised),
        refined_entries=sum(len(r["steps"]) for r in recipes),
        explicit_patch_groups=len(config["patches"]),
        corrected_entries=len(changes),
        review_approved_count=0,
    )
    dump(OUT / "build_summary.json", stats)
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
