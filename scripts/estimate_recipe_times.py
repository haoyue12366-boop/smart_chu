"""为用户指定的100道菜谱补充可追溯的AI时间估计，不生成审核记录。"""

from __future__ import annotations

import copy
import csv
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs/recipes_100_详细步骤_完善版.csv"
DETAIL = ROOT / "data/revisions/recipes_v2/recipes_100_完善版.json"
OUT = ROOT / "data/revisions/recipes_v3"
# 人工逐菜选择的秒数；只用于源时间未知的步骤。按固定源行定位后绑定到原ID。
CURATED = """
1:5=900,6=240,7=90
2:13=1500
3:6=1080
4:4=180,5=120,7=900
5:1=43200,4=3600,7=120
6:6=60,7=180,8=600,15=1080
7:6=600
8:10=1500
9:5=360,7=900,8=180
10:8=120
11:10=120
12:3=240,4=90,6=1200
14:7=2100
15:3=1800,6=600,11=1500,13=1800
16:4=180,6=600,10=480
19:5=180,8=720
20:3=1080
21:2=1200,4=180,5=30
22:2=240,3=180,5=1800,6=1800,19=1500,21=1200
23:6=5400
24:3=1200
25:3=300,7=300,8=180,9=60,10=180,13=30,15=2700
26:5=180,8=720
27:11=1080,13=1800,14=129600
28:6=720
29:6=420,8=900
30:3=480,4=240,12=1800
31:4=1200,6=300,8=1200,12=2400
32:14=1800
33:6=720
34:2=1080
35:2=1200,3=420,7=600
36:2=1200,4=600,7=300
37:9=900
38:7=1500
39:1=600,5=7200
40:2=180,4=1200,6=3600,10=1800
41:1=120,2=180,4=1200
42:7=2100,9=3600,13=300,16=7200
43:5=360,7=900,8=180
44:7=1800,9=2100
45:2=240,8=900
46:5=600,7=120
47:6=180,12=900
48:3=120
49:3=1800,6=300,10=5400
50:7=900,8=120
51:2=180,6=1200
52:6=1200,8=1500,10=180,11=180,13=180,15=1200,20=600,22=600
53:6=600,8=120
54:4=900
55:3=240,4=120,5=2100
56:7=1500
57:6=300
58:1=180,2=600,8=1500
59:2=1800,7=1800,9=900,12=1200,16=7200
60:13=1080,16=1200
62:5=1080
63:1=600,2=300
64:9=1080
65:4=900,6=900
67:7=1800,9=2100
68:1=86400,7=600,9=5400,11=7200
69:3=10800
71:1=1800,4=720
73:8=480,9=900
75:10=1800,11=120,12=900
76:2=3600,4=7200,5=1680
77:7=300,9=1200
78:1=180,3=300,8=2400,10=3600,12=300,17=120,19=1200
79:6=1500,11=1800
80:1=43200,3=1200
81:1=180
82:5=3000,10=240,13=1200,16=900
83:9=3000
84:7=180,9=900
85:4=120,5=300,6=120
86:6=60,7=90,8=60,9=180,11=1200
87:5=300,6=60
88:4=180,6=600,9=480
89:2=300,4=1200,7=180,10=14400,12=2100
90:3=1800
91:20=600,22=1200,23=240,24=900
92:7=1800
94:3=600,7=480,12=900
95:4=1800,8=1080,10=1200,11=120
96:4=1200,6=1200
97:1=300,6=1800,8=3600
98:5=180,9=480
99:5=120,6=60,7=90
100:5=3600,15=900
"""
# 批量整形、复杂切配等按本菜总量另估，避免将每个耗时误当作整批耗时。
MANUAL = """
1:3=540,8=360
3:3=480,4=600,5=180
13:5=360,6=300,7=120,8=240
15:2=600,4=300,14=180,15=360,16=360,17=300
18:3=360
22:7=600,9=180,11=480,13=300,15=300,17=300
27:6=240,7=360,8=720,9=360,10=240
35:4=180
40:1=300,5=300,7=480,8=480,9=720
42:4=360,10=180,11=300,12=240,14=420,15=600
52:9=240,16=600,17=1200,18=180,19=120,21=180,23=120
56:5=60,6=60
58:5=240
59:3=240,6=240,8=480,15=1200
60:15=300,17=420,18=240,19=300
61:10=180,11=180,12=180
64:1=1500,4=180,8=120
70:6=600
74:6=600
75:1=180,2=240,5=180,6=180,7=120,8=180,9=60,13=180
76:1=60,3=600
78:5=300,6=420,7=240,20=360,21=300,22=240
91:6=480,19=60,21=240,25=240
93:1=240,2=600,3=360,4=480,5=180,6=600,7=600,8=240,9=600,10=120,11=180,12=600,13=900,14=180,15=180
95:5=300,7=480
100:4=480,6=180,7=120,8=120,9=180,10=120,11=180,12=120
"""
RATIONALES = {
    "52:17": "40个烧麦按30秒/个成型，共1200秒；擀40张皮在第16步另计600秒。",
    "52:20": "第一批20个熟糯米馅烧麦，选择100℃有效蒸制10分钟作计划估计。",
    "52:22": "第二批20个烧麦沿用同批量10分钟；不得与第一批同时占同一蒸制设备。",
    "75:10": "套餐有效烹饪暂取45分钟，末段原文剩余15分钟，因此前段估计30分钟。",
    "75:11": "开门、加两道菜并关门预留120秒；估计程序计时暂停。",
    "75:12": "沿用原文剩余15分钟；前段45-15=30分钟是AI选择，非官方程序参数。",
    "91:20": "多层套餐独立预热估计10分钟；不计入后续35分钟有效烹饪。",
    "91:22": "固定套餐首段估计20分钟；以屏幕提示为实际介入触发条件。",
    "91:23": "五盘取出、两盘加料/覆盖及多盘翻拌后回装，共估计4分钟。",
    "91:24": "套餐末段估计15分钟；两段有效烹饪合计35分钟，介入另计。",
    "5:4": "按冷藏解冻后的单块牛排低温处理计划1小时；实际仍由原程序/终点控制。",
    "56:7": "冰鲜单块鸡胸肉探针程序计划25分钟；时间估计不能替代探针终点。",
    "69:3": "薄柠檬片脱水计划3小时；估计不代表设备程序实测时长。",
    "76:4": "仅冷冻分支：成型面团冷冻定型2小时；长期储存期限不是烹饪工时。",
    "76:5": "即食分支：预烤10分钟、人工铺馅3分钟、复烤15分钟；与冷冻分支互斥。",
    "78:19": "仅蛋糕未就绪时保温；20分钟为AI计划上限，实际按等待蛋糕就绪时长取值。",
    "93:13": "约28份油酥，按约30秒/份包馅加整形余量估计15分钟；份数和馅量以原配方为准。",
    "40:2": "备选微波方法采用原文约3分钟有效加热；分6轮，每轮30秒，轮间翻拌各15秒。",
}


def parse(text: str) -> dict[tuple[int, int], int]:
    result = {}
    for line in text.strip().splitlines():
        row, values = line.split(":")
        for item in values.split(","):
            step, seconds = item.split("=")
            result[(int(row), int(step))] = int(seconds)
    return result


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manual_seconds(step: dict) -> tuple[int, str]:
    text = step["text"]
    if re.search(r"完全扩展|拉出薄膜|手套膜", text):
        return 1200, "手工揉面至扩展状态按20分钟估计。"
    if "去骨" in text:
        return 480, "去骨属于精细处理，按小批量8分钟估计。"
    if re.search(r"开背|虾线", text):
        return 420, "按约300–400克虾逐只开背去线估计7分钟。"
    if re.search(r"去鳞|内脏|鱼鳃", text):
        return 480, "整鱼清理、冲洗按8分钟估计。"
    if re.search(r"打发|发泡", text):
        return 360, "使用电动打蛋器，人工全程操作约6分钟。"
    if re.search(r"揉|搓.*面团", text):
        return 360, "家庭单配方面团手工混合揉匀约6分钟。"
    if re.search(r"擀|面皮", text):
        return 300, "手工擀面、整理约5分钟；特殊批量另行覆盖。"
    if re.search(r"包入|包好|收口|馅心|压出|按压成型|小剂|搓圆", text):
        return 360, "按原配方一批分割/整形估计6分钟；明确件数使用逐菜覆盖。"
    if re.search(r"切碎|切丝|切丁|剁|去皮|切片|切块|切段", text):
        count = len(re.findall(r"切碎|切丝|切丁|剁|去皮|切片|切块|切段", text))
        return min(360, 120 + 60 * count), "按原量一批清理切配，操作种类每增加一项预留1分钟。"
    if re.search(r"筛|碾|压成泥|压烂", text):
        return 180, "过筛、压泥或细化一批约3分钟。"
    if re.search(r"刷|裱|撒|铺|码|摆|填", text):
        return (180 if len(text) > 65 else 120), "按原量摆盘、刷涂或铺料估计2–3分钟。"
    if re.search(r"洗|清理", text):
        return 180, "原配方一批清洗和沥水操作约3分钟。"
    if re.search(r"混|搅|拌|均匀", text):
        return (180 if len(text) > 80 else 120), "配料加入并混合均匀按2–3分钟估计。"
    if re.search(r"取出水箱|加满水", text):
        return 90, "取水箱、补水、回装和按键约90秒。"
    if re.search(r"选择|设置|开启电源|插头|探针", text):
        return 60, "设备设置或探针连接按1分钟估计。"
    if re.search(r"取出|放入|装入|盛出|倒入|倒出|关火|打开|关上|封|盖", text):
        return (60 if len(text) > 45 else 30), "短时转移、开关门或覆盖操作按30–60秒估计。"
    return 120, "按本菜一批简单人工操作预留2分钟；属于AI估计。"


def choose_time(step: dict, row: int, curated: dict, manual: dict) -> tuple[int, str, str]:
    key = (row, step["display_step"])
    constraint = step["source_time_constraint"]
    relation = constraint["relation"]
    if relation == "exact_source":
        return constraint["lower_sec"], "SOURCE_EXPLICIT", "原文明确工艺时间，保持不变。"
    if relation == "approximate":
        return (
            constraint["nominal_sec"],
            "SOURCE_EXPLICIT",
            "原文约数作为计划名义值，保留约数语义。",
        )
    if relation == "minimum":
        return (
            constraint["lower_sec"],
            "MODEL_SUGGESTION",
            "计划暂取原文时间下界；实际仍须满足原文状态终点。",
        )
    if relation == "range":
        lo, hi = constraint["lower_sec"], constraint["upper_sec"]
        return (lo + hi) // 2, "MODEL_SUGGESTION", "计划取原文区间中值，原始上下界完整保留。"
    if key in manual and step["kind"] == "人工":
        return (
            manual[key],
            "MODEL_SUGGESTION",
            RATIONALES.get(
                f"{row}:{key[1]}", "按本菜配方总量、批量件数和操作复杂度单独估计人工工时。"
            ),
        )
    if step["kind"] == "人工":
        seconds, reason = manual_seconds(step)
        return seconds, "MODEL_SUGGESTION", reason
    if key not in curated:
        raise ValueError(f"未逐项估计的非人工工序：{row} {key} {step['text']}")
    reason = RATIONALES.get(
        f"{row}:{key[1]}",
        f"按本菜原配方一批、步骤终点和工艺类型估计{curated[key]}秒；不是设备预置程序实测值。",
    )
    return curated[key], "MODEL_SUGGESTION", reason


def phase(kind: str, seconds: int, human: bool, origin: str = "MODEL_SUGGESTION") -> dict:
    return {"kind": kind, "duration_sec": seconds, "human": human, "origin": origin}


def timing(step: dict, recipe: dict, row: int, curated: dict, manual: dict) -> dict | None:
    if step["kind"] == "说明":
        return None
    seconds, origin, reason = choose_time(step, row, curated, manual)
    text, resource, kind = step["text"], step["resource"], step["kind"]
    number = step["display_step"]
    key = (row, number)
    optional = kind in ("可选等待", "可选方法", "条件分支")
    phases = []
    interventions = []
    is_wait = kind in ("等待", "可选等待", "条件分支")
    chamber = bool(
        re.search(r"蒸箱|烤箱|蒸烤|蒸制|烤制|微波|低温处理|程序", resource + text)
    ) and not re.search(r"炒锅|平底锅|灶具|电磁灶|燃气灶", resource)
    if key == (40, 2):
        for cycle in range(6):
            phases.append(phase("MICROWAVE", 30, False, "SOURCE_EXPLICIT"))
            if cycle < 5:
                phases.append(phase("STIR", 15, True))
        reason = RATIONALES["40:2"]
        origin = "SOURCE_EXPLICIT"
    elif key == (76, 5):
        phases = [
            phase("OVEN_PREHEAT", 600, False),
            phase("LOAD", 60, True),
            phase("BAKE_BASE", 600, False),
            phase("ADD_TOPPING", 180, True),
            phase("BAKE_FINISH", 900, False),
            phase("UNLOAD", 60, True),
        ]
    elif is_wait:
        if key == (76, 4):
            phases.append(phase("PACKAGE", 60, True))
        phases.append(phase("PASSIVE_WAIT", seconds, False, origin))
    elif kind in ("人工", "中途介入") or (key == (95, 11)):
        phases.append(phase("ACTIVE", seconds, True, origin))
    elif chamber:
        if key in ((91, 20), (77, 7)):
            phases.append(phase("PREHEAT", seconds, False, origin))
        else:
            setup = 0
            if re.search(r"选择|设置|开启电源|开始烹饪", text):
                setup += 60
            if re.search(r"加满水|水箱", text):
                setup += 60
            if re.search(r"放入|入机器|入蒸箱", text):
                setup += 60
            if setup:
                phases.append(phase("SETUP_LOAD", setup, True))
            prior = recipe["steps"][: number - 1]
            had_heat = any(
                p["kind"] in ("热加工", "复合工序")
                and re.search(r"蒸箱|烤箱|蒸烤|蒸制|烤制|程序", p["resource"] + p["text"])
                and not re.search(r"炒锅|平底锅|灶具", p["resource"])
                for p in prior
            )
            if key in ((75, 10), (75, 12), (91, 22), (91, 24), (77, 9)):
                warmup = 300 if key == (75, 10) else 0
            elif "微波" in text + resource or "无需预热" in text:
                warmup = 0
            elif had_heat:
                warmup = 60 if "蒸" in text + resource else 120
            else:
                warmup = 300 if "蒸" in resource and "烤" not in resource else 600
            if warmup:
                phases.append(phase("WARMUP_OR_RECOVERY", warmup, False))
            phases.append(phase("PROCESS", seconds, False, origin))
            if re.search(r"结束后.*取出|取出.*食用|取出即可", text):
                phases.append(phase("UNLOAD", 60, True))
    else:
        # 主动炒、煎、搅拌全程占唯一人工；长炖煮分离检查时段。
        if seconds > 600 and re.search(r"煮|炖", text) and not re.search(r"不停搅|边.*搅", text):
            phases.append(phase("PROCESS", seconds, False, origin))
            interventions = [{"offset_sec": 0, "duration_sec": 30, "action": "检查火力"}]
            for offset in range(600, seconds - 30, 600):
                interventions.append(
                    {"offset_sec": offset, "duration_sec": 30, "action": "检查/按原文翻拌"}
                )
        else:
            phases.append(phase("PROCESS_ACTIVE", seconds, True, origin))
    elapsed = sum(p["duration_sec"] for p in phases)
    active = sum(p["duration_sec"] for p in phases if p["human"])
    active += sum(p["duration_sec"] for p in interventions)
    # 翻面包含在连续占用工时中，不能再叠加30秒。
    if key == (87, 5):
        interventions.append(
            {
                "offset_sec": 150,
                "duration_sec": 30,
                "action": "香菇翻面",
                "included_in_continuous_human": True,
            }
        )
    for p in phases:
        assert p["duration_sec"] > 0
    assert 0 <= active <= elapsed
    return {
        "nominal_process_sec": seconds,
        "process_origin": origin,
        "elapsed_sec": elapsed,
        "human_active_sec": active,
        "human_resource_id": "human_1" if active else None,
        "phases": phases,
        "interventions": interventions,
        "optional": optional,
        "basis": reason,
        "review_status": "NEEDS_REVIEW",
        "approved": False,
        "uncertainty": "AI估计供开发排程使用；原文时间、设备终点和批次约束仍保留",
        "process_range_sec": (
            [
                step["source_time_constraint"]["lower_sec"],
                step["source_time_constraint"]["upper_sec"],
            ]
            if step["source_time_constraint"]["relation"] == "range"
            else None
        ),
    }


def duration_label(seconds: int) -> str:
    if seconds % 3600 == 0:
        return f"{seconds // 3600}小时"
    if seconds % 60 == 0:
        return f"{seconds // 60}分钟"
    return f"{seconds}秒"


def render_step(step: dict) -> str:
    number = step["display_step"]
    plan = step.get("timing_plan")
    if plan is None:
        return f"第{number}步【说明，不单独计时】{step['text']}"
    source = "原文" if plan["process_origin"] == "SOURCE_EXPLICIT" else "AI估计"
    constraint = step["source_time_constraint"]
    retained = ""
    if constraint["relation"] in ("range", "minimum", "approximate"):
        retained = f"；原文限制：{constraint['text']}"
    optional = "；可选/条件路径，不能与互斥路径累计" if plan["optional"] else ""
    parts = []
    labels = {
        "ACTIVE": "人工操作",
        "SETUP_LOAD": "设置/装料",
        "PREHEAT": "预热",
        "WARMUP_OR_RECOVERY": "升温/温度恢复",
        "PROCESS": "设备有效运行",
        "PROCESS_ACTIVE": "人工与加工同步",
        "PASSIVE_WAIT": "被动等待",
        "UNLOAD": "取出",
        "MICROWAVE": "微波",
        "STIR": "翻拌",
        "OVEN_PREHEAT": "烤箱预热",
        "LOAD": "装料",
        "BAKE_BASE": "预烤",
        "ADD_TOPPING": "加馅",
        "BAKE_FINISH": "复烤",
        "PACKAGE": "包装",
    }
    for p in plan["phases"]:
        parts.append(
            f"{labels[p['kind']]}{duration_label(p['duration_sec'])}"
            f"（{'原文' if p['origin'] == 'SOURCE_EXPLICIT' else 'AI估计'}）"
        )
    event = ""
    if plan["interventions"]:
        event = (
            "；运行内介入："
            + "、".join(
                f"{e['offset_sec']}秒时{e['action']}{e['duration_sec']}秒"
                for e in plan["interventions"]
            )
            + "（AI估计，已含于本步）"
        )
    body = step["text"]
    if (step.get("recipe_index"), number) == (75, 10):
        body += " 本版计划有效烹饪45分钟，前段30分钟；程序计时不含估计的升温和开门加料。"
    if (step.get("recipe_index"), number) == (78, 19):
        body += " 实际等待到蛋糕就绪即结束，20分钟是计划上限而非必需固定等待。"
    return (
        f"第{number}步【{step['kind']}｜工艺名义时间：{duration_label(plan['nominal_process_sec'])}"
        f"（{source}）{retained}；分段：{'，'.join(parts)}；本步合计："
        f"{duration_label(plan['elapsed_sec'])}；人工占用：{plan['human_active_sec']}秒"
        f"；设备/资源：{step['resource']}{optional}{event}】{body}"
    )


def main() -> None:
    base = json.loads(DETAIL.read_text(encoding="utf-8"))
    with SOURCE.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert rows == base["rows"], "指定的CSV与结构化步骤不一致，停止，避免套错时间"
    curated, manual = parse(CURATED), parse(MANUAL)
    result = copy.deepcopy(base)
    result["version"] = "recipes-v3-ai-timing-1"
    result["source_hashes"][SOURCE.relative_to(ROOT).as_posix()] = sha(SOURCE)
    result["source_hashes"][DETAIL.relative_to(ROOT).as_posix()] = sha(DETAIL)
    result["scope"] = "100道原ID；补齐步骤计划时间；AI估计不等于人工审核或实测"
    assumptions = []
    for index, recipe in enumerate(result["recipes"], 1):
        for step in recipe["steps"]:
            step["recipe_index"] = index
            step["timing_plan"] = timing(step, recipe, index, curated, manual)
            if step["timing_plan"] is not None:
                assumptions.append(
                    {
                        "recipe_id": recipe["recipe_id"],
                        "name": recipe["name"],
                        "step": step["display_step"],
                        "text": step["text"],
                        **step["timing_plan"],
                    }
                )
        result["rows"][index - 1]["烹饪步骤"] = "".join(render_step(s) for s in recipe["steps"])
    OUT.mkdir(parents=True, exist_ok=True)
    for name, content in [
        ("recipes_100_时间补全版.json", result),
        (
            "time_estimates.json",
            {
                "version": result["version"],
                "source_sha256": sha(SOURCE),
                "author": "AI assistant",
                "origin": "MODEL_SUGGESTION",
                "approved": False,
                "assumptions": assumptions,
                "method": "固定原配方用量；秒级分段；明确工艺时间保持；缺失值逐菜/按动作估计。",
            },
        ),
    ]:
        (OUT / name).write_text(
            json.dumps(content, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(
        json.dumps(
            {
                "recipes": len(rows),
                "timed_steps": len(assumptions),
                "estimated_primary_times": sum(
                    a["process_origin"] == "MODEL_SUGGESTION" for a in assumptions
                ),
                "source_primary_times": sum(
                    a["process_origin"] == "SOURCE_EXPLICIT" for a in assumptions
                ),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
