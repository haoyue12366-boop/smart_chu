"""将菜谱修订稿转换为有明确时间、资源和DAG的开发调度数据包。"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path

from app.domain.canonical_recipe import CanonicalRecipeModel
from scripts.update_p0_samples_from_revision import ingredients

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/revisions/recipes_v3"
INPUT = OUT / "recipes_100_时间补全版.json"
VERSION = "development-scheduling-v3"
# 默认屏端程序的AI建议配置，不冒充官方预置值；有明确源参数的步骤优先用原文。
OVEN = {
    1: 180,
    2: 180,
    3: 180,
    8: 180,
    13: 180,
    14: 190,
    17: 220,
    18: 170,
    20: 180,
    22: 180,
    24: 180,
    27: 180,
    34: 200,
    35: 200,
    40: 180,
    41: 170,
    42: 170,
    44: 190,
    45: 200,
    47: 180,
    48: 180,
    51: 200,
    56: 180,
    58: 180,
    59: 170,
    60: 170,
    61: 180,
    64: 180,
    66: 230,
    67: 190,
    69: 80,
    70: 180,
    72: 200,
    74: 180,
    75: 180,
    76: 180,
    78: 170,
    80: 200,
    81: 150,
    83: 180,
    86: 200,
    87: 190,
    91: 180,
    93: 180,
    94: 200,
    95: 170,
    97: 220,
    99: 200,
}
# 显式拆分资源切换和错误复合分类。元组：动作、说明、秒、设备类型、主动人工。
CUSTOM = {
    (9, 5): [
        ("ADD", "锅中加入双椒酱原配方油与辅料", 30, "stove", True),
        ("HEAT", "煸炒双椒葱姜蒜豆豉并按原配方调味勾芡", 300, "stove", True),
        ("TRANSFER", "关火后将热双椒酱淋在腌好鱼头上", 30, "stove", True),
    ],
    (19, 5): [
        ("ADD", "锅中加入豆豉蒜末红椒和原配方食用油", 30, "stove", True),
        ("HEAT", "将豆豉蒜椒炒香", 120, "stove", True),
        ("TRANSFER", "关火后将炒香豆豉蒜椒淋在盘中腐竹上", 30, "stove", True),
    ],
    (10, 8): [
        ("ADD", "锅中加入原配方食用油", 30, "stove", True),
        ("HEAT", "加热食用油", 60, "stove", False),
        ("TRANSFER", "关火后将热油淋在鲈鱼背上", 30, "stove", True),
    ],
    (9, 8): [
        ("UNLOAD", "取出蒸好的鱼头", 60, None, True),
        ("ADD", "向锅中加入辣椒油", 30, "stove", True),
        ("HEAT", "加热辣椒油", 90, "stove", False),
        ("TRANSFER", "关火后将热油淋在鱼身", 60, "stove", True),
    ],
    (11, 10): [
        ("ADD", "向炒锅加入食用油", 30, "stove", True),
        ("HEAT", "加热食用油", 90, "stove", False),
        ("TRANSFER", "关火移锅并淋葱姜丝", 30, "stove", True),
    ],
    (12, 4): [
        ("TRANSFER", "将热椰浆糖水倒入粉浆", 30, None, True),
        ("MIX", "快速搅匀烫成熟浆", 60, None, True),
    ],
    (21, 5): [("STIR", "翻面并关闭火源", 30, "stove", True)],
    (31, 3): [
        ("LOAD", "将浸泡好的红枣连同浸泡液装入蒸箱", 60, "steam", True),
        ("PREHEAT", "蒸箱升温至100℃", 300, "steam", False),
        ("HEAT", "100℃蒸红枣30分钟", 1800, "steam", False),
        ("UNLOAD", "取出熟红枣和浸泡液", 60, "steam", True),
    ],
    (35, 3): [
        ("CUT", "40克蒜瓣去皮切蒜蓉", 180, None, True),
        ("ADD", "锅中倒入30克油", 30, "stove", True),
        ("HEAT", "加热油锅", 60, "stove", False),
        ("HEAT", "加入蒜末小火炒至浅金黄", 120, "stove", True),
        ("TRANSFER", "关火盛出蒜蓉", 30, "stove", True),
        ("MIX", "加入原配方酱汁与水调匀", 60, None, True),
    ],
    (38, 2): [
        ("WASH", "排骨放入盆中冲洗", 60, None, True),
        ("WAIT", "清水浸洗排骨", 480, None, False),
        ("WASH", "复洗并沥干排骨", 60, None, True),
    ],
    (40, 1): [
        ("ADD", "30克糯米粉倒入干净炒锅", 30, "stove", True),
        ("HEAT", "慢火连续翻炒手粉至微黄", 240, "stove", True),
        ("UNLOAD", "关火盛出手粉", 30, "stove", True),
    ],
    (43, 8): [
        ("UNLOAD", "取出蒸好的鱼头", 60, None, True),
        ("ADD", "向锅中加入辣椒油", 30, "stove", True),
        ("HEAT", "加热辣椒油", 90, "stove", False),
        ("TRANSFER", "关火后将热油淋在鱼身", 60, "stove", True),
    ],
    (46, 7): [
        ("ADD", "向炒锅加入食用油", 30, "stove", True),
        ("HEAT", "加热食用油", 90, "stove", False),
        ("TRANSFER", "关火后淋鱼身", 30, "stove", True),
    ],
    (76, 5): [
        ("PREHEAT", "烤箱预热至180℃", 600, "oven", False),
        ("LOAD", "装入成型披萨胚", 60, "oven", True),
        ("HEAT", "180℃预烤披萨胚", 600, "oven", False),
        ("UNLOAD", "取出披萨胚", 60, "oven", True),
        ("ADD", "按原文填入馅料并撒芝士", 180, None, True),
        ("LOAD", "回装披萨胚", 60, "oven", True),
        ("HEAT", "180℃复烤至熟", 900, "oven", False),
        ("UNLOAD", "取出披萨", 60, "oven", True),
    ],
    (91, 3): [
        ("HEAT", "锅内烧开冲烫肥牛用水", 300, "stove", False),
        ("WASH", "肥牛用开水冲烫两遍后洗净血水", 120, None, True),
    ],
}
# 每菜前置任务，和源工序连接。分支外的原料不需要伪造已完成记录。
CUSTOM[26, 5] = copy.deepcopy(CUSTOM[19, 5])
CUSTOM[43, 5] = copy.deepcopy(CUSTOM[9, 5])
CUSTOM[43, 8] = copy.deepcopy(CUSTOM[9, 8])

PREPARATION = {
    1: [
        (
            6,
            [
                ("WASH", "淘洗60克生米，加入90克水，制成约150克熟米饭", 120, None, True),
                ("LOAD", "米水装入蒸箱", 60, "steam", True),
                ("HEAT", "普通蒸100℃蒸米饭", 1500, "steam", False),
                ("UNLOAD", "取出并拨松米饭", 60, "steam", True),
            ],
        )
    ],
    5: [
        (
            9,
            [
                ("WASH", "补充西兰花50克并洗净切小朵", 180, None, True),
                ("HEAT", "锅内烧开焯西兰花用水", 300, "stove", False),
                ("HEAT", "西兰花入沸水焯熟", 180, "stove", True),
                ("UNLOAD", "捞出沥干西兰花", 30, "stove", True),
            ],
        )
    ],
    52: [
        (
            11,
            [
                ("WAIT", "原料中的10克干香菇温水泡发", 1800, None, False),
                ("CUT", "泡发香菇洗净切丁，和原葱姜一起炒入馅料", 120, None, True),
            ],
        )
    ],
    75: [
        (
            1,
            [
                ("WASH", "整鸡洗净沥干，加入奥尔良腌料80克和水70克揉匀", 600, None, True),
                ("MARINATE", "鸡在4℃冷藏腌制", 14400, "cold", False),
            ],
        ),
        (5, [("WASH", "逐只刷洗500克小龙虾，去虾线并沥干", 900, None, True)]),
        (2, [("WASH", "排骨洗净沥干", 300, None, True)]),
        (6, [("CUT", "土豆、胡萝卜洗净切丝，茼蒿择洗沥干", 480, None, True)]),
    ],
    76: [
        (
            1,
            [
                ("MIX", "按配方将高筋面粉、水、酵母、糖、盐和橄榄油混合", 180, None, True),
                ("PREPARE", "揉面至光滑面团", 900, None, True),
            ],
        )
    ],
    91: [
        (
            2,
            [
                ("WASH", "淘洗160克生米，加入240克水，制备约400克熟米饭", 120, None, True),
                ("LOAD", "米水装入蒸箱", 60, "steam", True),
                ("HEAT", "普通蒸100℃蒸米饭", 1500, "steam", False),
                ("UNLOAD", "取出米饭并拨松", 60, "steam", True),
            ],
        )
    ],
}
ADAPTATIONS = {
    5: [
        "低温牛排选鲜嫩蒸60℃60分钟作开发计划，再按原文煎制；增加西兰花50克及焯制。",
        "低温完成仍应核对原工艺和中心状态；估时不是食用安全验证。",
    ],
    11: ["原微波中高火2分钟替换为普通蒸100℃2分钟；属于AI建议替代，原蒸8分钟保持。"],
    12: ["原9档煮沸改用清单支持的7档；升温时间重新估计。"],
    30: ["原9档大火改为7档，不声称两者功率等同。"],
    40: ["默认选择炒锅制作熟糯米粉；微波备选路径保留在备选说明中，不排入默认路径。"],
    41: ["原上下火180/165℃独立控温改为上下火统一170℃，有效烘烤仍35分钟；AI替代。"],
    49: ["原8档大火改为7档，保留原文对照。"],
    52: [
        "两批各20个；每批100℃蒸10分钟为AI估计。熟五花肉按原文复煮保留。",
        "补充原料列已有但步骤遗漏的干香菇泡发切丁及入馅动作。",
    ],
    64: ["设备未列35–40℃发酵能力；转为有盖容器常温发酵，按原区间上限规划并保留两倍大终点。"],
    66: ["原240℃超出上限；替代为顶部烤230℃90秒，原1分钟保留作历史证据。"],
    70: ["设备未列35℃发酵能力；改常温加盖发酵60分钟，以发酵状态为实际终点。"],
    74: ["原35℃两段发酵改常温60分钟/40分钟，不虚构烤箱低温能力。"],
    75: [
        "固定套餐选加湿烤180℃中湿度作开发替代；预热5分钟，热运行30+15分钟，介入暂停2分钟。",
        "补充鸡腌制、小龙虾清理、排骨清洗、蔬菜切丝和正文明确的米200克、水240克。",
    ],
    76: [
        "默认选择即食烤制分支；按AI假设补充馅料蔬菜100克、芝士100克，保留冷冻备选。",
        "补充从面粉开始混合揉面；按400克面粉的一批全部制作，整形时间按整批。",
    ],
    77: ["预热5分钟与普通蒸100℃20分钟分开，屏端自动程序参数属于AI建议。"],
    78: ["默认并行准备珍珠和蛋糕，水浴保温为条件分支，实际等待由蛋糕就绪决定。"],
    80: ["调味法式羊排按原料列作为购入调味生制品，不虚构再次腌制；4℃冷藏解冻12小时。"],
    86: ["原8档大火改为7档，升温和操作按拆分估时。"],
    91: [
        "固定套餐采用加湿烤180℃中湿度作开发替代：预热10分钟、热运行20+15分钟、介入4分钟。",
        "400克熟米饭由160克生米和240克水制备；默认无已完成备料库存。",
    ],
}
# 原料与单菜路径补全不改源CSV的身份/原食材文本；另列显式增补。
SUPPLEMENTS = {
    1: ["将原150克熟米饭展开为生米60克+水90克的AI用量假设"],
    5: ["西兰花50克，依据原文成品装饰需求补充的AI用量"],
    75: ["大米200克、水240克，来自原步骤；不是AI猜测"],
    76: ["披萨馅料：蔬菜100克、芝士100克，默认即食路径的AI用量假设"],
    91: ["将原400克熟米饭展开为生米160克+水240克的AI用量假设"],
}


CUSTOM[(60, 14)] = [
    ("UNLOAD", "取出烤好的抹茶蛋糕放网架", 60, "oven", True),
    ("WAIT", "将抹茶蛋糕充分放凉", 1800, None, False),
    ("CUT", "切成两片20乘5厘米蛋糕片", 120, None, True),
]
CUSTOM[(92, 9)] = [
    ("PREHEAT", "炒锅加油预热", 90, "stove", False),
    ("HEAT", "下入腌好猪肉丁炒熟", 180, "stove", True),
    ("HEAT", "加入香菇胡萝卜葱姜和C调味料炒熟", 120, "stove", True),
    ("HEAT", "加入已蒸熟的糯米拌炒均匀", 120, "stove", True),
]
PREPARATION[60] = [
    (
        3,
        [
            ("WAIT", "用冷水泡发吉利丁5克", 600, None, False),
            ("PREPARE", "沥去吉利丁表面水分", 30, None, True),
        ],
    ),
    (7, [("HEAT", "隔水融化15克黄油", 180, "stove", False)]),
    (9, [("PREPARE", "将4个鸡蛋蛋清蛋黄分离", 120, None, True)]),
]
OVEN[6] = 180
ADAPTATIONS[6] = ["蒸味/香茅鳗鱼固定同烹改用加湿烤180℃中湿度18分钟；AI替代，保留两种配味。"]
ADAPTATIONS[98] = ["原50克蒜末明确分配熟蒜25克、生蒜25克；原调味汁再次使用50克的矛盾保留对照。"]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def config(device: str | None, row: int, text: str) -> list[dict]:
    if device is None:
        return []
    if device == "stove":
        match = re.search(r"([1-9])(?:档|段)", text)
        power = (
            min(7, int(match[1]))
            if match
            else (2 if "小火" in text else 5 if "中火" in text else 7)
        )
        return [{"parameter": "power_level", "value": power}]
    if device in ("cold", "freezer"):
        return [
            {"parameter": "temperature_c", "value": 4 if device == "cold" else -18},
            {"parameter": "mode", "value": "冷藏" if device == "cold" else "冷冻"},
        ]
    temp_match = re.search(r"(\d{2,3})\s*℃", text)
    if device == "steam":
        temp = int(temp_match[1]) if temp_match else (60 if row == 5 else 100)
        temp = min(100, max(30, temp))
        return [
            {"parameter": "temperature_c", "value": temp},
            {"parameter": "mode", "value": "鲜嫩蒸" if temp <= 90 else "普通蒸"},
        ]
    temp = int(temp_match[1]) if temp_match else OVEN.get(row, 180)
    if row in (41, 66):
        temp = OVEN[row]
    mode = "上下火"
    if row == 69:
        mode = "蔬果干"
    elif row in (6, 75, 91):
        mode = "加湿烤"
    elif row == 66 or "完全烧烤" in text:
        mode = "顶部烤"
    elif "鼓风" in text:
        mode = "鼓风烤"
    elif "全开" in text:
        mode = "全开烤"
    values = [{"parameter": "temperature_c", "value": temp}, {"parameter": "mode", "value": mode}]
    if mode == "加湿烤":
        values.append({"parameter": "humidity", "value": "中"})
    return values


def device_for(step: dict, row: int) -> str | None:
    text = step["resource"] + step["text"]
    if step["kind"] in ("等待", "可选等待"):
        if "冷冻" in text:
            return "freezer"
        if re.search(r"冷藏|解冻|腌", text):
            return "cold"
        return None
    if re.search(r"炒锅|平底锅|灶具|电磁灶|燃气灶|锅中|锅内|煸|焯|汆烫|热水软化|隔水|隔热水", text):
        return "stove"
    if "蒸箱" in text:
        return "steam"
    if "烤箱" in text:
        return "oven"
    if (row, step["display_step"]) in ((11, 8), (21, 2), (40, 4), (60, 2), (60, 8)):
        return "steam"
    if row == 5:
        return "steam"
    if row in OVEN:
        return "oven"
    return "steam"


def resource_use(device: str, row: int, text: str, ref: str) -> dict:
    rid = {
        "stove": "stove_choice",
        "steam": "steam_oven_1",
        "oven": "oven_1",
        "cold": "fridge_cold_1",
        "freezer": "fridge_freezer_1",
    }[device]
    physical = {
        "stove": "stove_1",
        "steam": "steam_oven_1",
        "oven": "oven_1",
        "cold": "fridge_1",
        "freezer": "fridge_1",
    }[device]
    component = {
        "stove": "selected_burner",
        "steam": "chamber",
        "oven": "chamber",
        "cold": "cold_zone",
        "freezer": "freezer_zone",
    }[device]
    return dict(
        resource_type="DEVICE",
        resource_id=rid,
        physical_resource_id=physical,
        component_id=component,
        conflict_policy="STATE_COMPATIBLE" if device in ("cold", "freezer") else "UNARY",
        configuration=config(device, row, text),
        evidence_refs=[ref],
        rule_version=VERSION,
        review_status="NEEDS_REVIEW",
    )


def action_for(text: str) -> str:
    # 取出水箱补水不是卸下食物；先排除这个设备维护动作。
    text = re.sub(r"取出水箱(?:加满水)?", "", text)
    if re.search(r"切|剁|削", text):
        return "CUT"
    if re.search(r"洗|清理", text):
        return "WASH"
    if re.search(r"混|搅|拌|揉", text):
        return "MIX"
    if re.search(r"取出|盛出|捞出", text):
        return "UNLOAD"
    if re.search(r"装入|放入|放进", text):
        return "LOAD"
    return "PREPARE"


def source_edges(recipe: dict, valid: set[int], mapping: dict) -> list[list[int]]:
    rid = recipe["recipe_id"]
    if rid in mapping["recipes"]:
        chains = mapping["recipes"][rid]["chains"]
        edges = {
            (a, b)
            for chain in chains
            for a, b in zip(chain, chain[1:], strict=False)
            if a in valid and b in valid
        }
    else:
        ordered = sorted(valid)
        edges = set(zip(ordered, ordered[1:], strict=False))
    # 文本明确的等待期间处理分支，单独记录；其他路径采用保守完整顺序。
    branches = {
        64: [[1, 2, 3, 4, 5, 8, 9], [6, 7, 8]],
        78: [
            [1, 2, 3, 5, 7, 8, 9, 10, 11, 21, 22],
            [4, 5],
            [4, 6, 7],
            [12, 13, 14, 15, 16, 17, 18, 21],
            [20, 21],
        ],
        89: [[2, 3, 4, 13], [5, 6, 7, 8, 9, 11, 13], [10, 12, 13]],
    }
    if recipe["csv_row"] - 1 in branches:
        edges = {
            (a, b)
            for chain in branches[recipe["csv_row"] - 1]
            for a, b in zip(chain, chain[1:], strict=False)
            if a in valid and b in valid
        }
    # 无法从过滤后的说明/可选步骤获得的节点也保留在DAG中，最终完成依赖全部终点。
    return [list(e) for e in sorted(edges)]


def effective_record(recipe: dict, row: int) -> dict:
    record = dict(recipe["original_record"])
    text = record["食材清单"]
    if row == 1:
        text = text.replace("米饭150克", "生米60克；水90克")
    elif row == 5:
        text += "；补充：西兰花50克"
    elif row == 75:
        text += "；补充：大米200克；水240克"
    elif row == 76:
        text += "；补充：蔬菜100克；芝士100克"
    elif row == 91:
        text = text.replace("熟米饭400克", "生米160克；水240克")
    if row == 68:
        text = text.split("主料：", 1)[0].rstrip("；")
    if row == 85:
        text = text.replace("水180；", "水180克；")
    additions = {
        2: "水50克",
        16: "小葱1根",
        22: "淡奶油100克；时令水果100克",
        23: "水1000毫升",
        25: "水400毫升",
        33: "白醋5毫升",
        50: "食用油10克",
        60: "玉米淀粉10克",
        79: "食用油5克",
        85: "食用油10克",
        86: "料酒10克",
        88: "小葱1根",
        89: "水400毫升",
        96: "水30毫升",
    }
    if row in additions:
        text += "；补充：" + additions[row]
    if row == 32:
        text = text.replace("盐25克", "盐2.5克")
    if row == 91:
        text = text.replace("生抽35克", "生抽38克")
    record["食材清单"] = text
    return record


def convert(recipe: dict, row: int, mapping: dict) -> dict:
    rid = recipe["recipe_id"]
    evidence = []
    source = {
        "recipe_id": rid,
        "source_record": effective_record(recipe, row),
        "source_file": "data/revisions/recipes_v3/ingredient_inputs.json",
    }
    specs, requirements = ingredients(source, evidence)
    original_ingredients = recipe["original_record"]["食材清单"].split("；")
    original_path = ROOT / "docs/recipes_100.csv"
    for item in evidence:
        if item["text_span"] in original_ingredients:
            item["origin"] = "SOURCE_EXPLICIT"
            item["source_file"] = original_path.relative_to(ROOT).as_posix()
            item["source_hash"] = sha(original_path)
            item["artifact_ref"] = dict(
                path=item["source_file"], sha256=item["source_hash"], media_type="text/csv"
            )
        else:
            item["origin"] = "MODEL_SUGGESTION"
    for spec in specs:
        spec["state"] = "配方采购原料：" + spec["name"] + "；清洗、切配及熟制见后续工序"

    ops, metadata, dependencies, groups = [], {}, [], {}
    reservations, constraints = [], []
    alternatives = []
    ref = f"ai-timing:{rid}"
    material_map = next(
        e
        for f in sorted(OUT.glob("material_map_*.json"))
        for e in json.loads(f.read_text(encoding="utf-8"))
        if e["row"] == row
    )
    initialized_devices = set()
    op_device = {}

    def emit(
        number: int, pieces: list[tuple], why: str, source_constraint: dict | None = None
    ) -> list[str]:
        ids = []
        for count, (action, description, duration, device, human) in enumerate(pieces, 1):
            ident = f"op_{number:03}_{count:02}"
            refs = [ref]
            uses = []
            if human:
                uses.append(
                    dict(
                        resource_type="HUMAN",
                        resource_id="human_1",
                        units=1,
                        conflict_policy="UNARY",
                        review_status="NEEDS_REVIEW",
                        evidence_refs=refs,
                    )
                )
            if device:
                uses.append(resource_use(device, row, description, ref))
                op_device[ident] = device
            policy = {}
            if row == 52 and number in (20, 22) and action == "HEAT":
                policy = {
                    "batch_policy": "FIXED_RECIPE",
                    "fixed_batch_id": f"{rid}:batch_{1 if number == 20 else 2}",
                }
            if row in (75, 91) and number in ((10, 11, 12) if row == 75 else (20, 21, 22, 23, 24)):
                policy["thermal_group_id"] = f"{rid}:fixed_program"
            duration_spec = dict(
                execution_sec=duration,
                nominal_sec=duration,
                source_ref=ref,
                fixed_process_time=action == "HEAT",
            )
            if source_constraint and action in ("HEAT", "WAIT", "FREEZE", "CHILL", "MARINATE"):
                c = source_constraint
                if c["relation"] == "exact_source" and c["lower_sec"] == duration:
                    duration_spec.update(lower_sec=duration, upper_sec=duration)
                elif c["relation"] in ("range", "minimum"):
                    duration_spec.update(lower_sec=c["lower_sec"], upper_sec=c["upper_sec"])
            ops.append(
                dict(
                    operation_id=ident,
                    action=action,
                    description=description,
                    duration=duration_spec,
                    resource_requirements=uses,
                    provenance_refs=refs,
                    execution_policy=policy,
                    review_status="NEEDS_REVIEW",
                )
            )
            metadata[ident] = {
                "source_step": number if number <= len(recipe["steps"]) else None,
                "origin": "MODEL_SUGGESTION",
                "basis": why,
                "input_state": f"{recipe['name']}配方批次，操作前：{description}",
                "output_state": f"已完成：{description}",
                "batch_reference": rid + ":one_source_batch",
                "source_time_constraint": source_constraint,
                "cross_recipe_sharing": False,
            }
            if ids:
                dependencies.append(
                    dict(
                        predecessor_id=ids[-1],
                        successor_id=ident,
                        min_lag_sec=0,
                        max_lag_sec=0,
                        reason="同一步骤拆分后的连续执行",
                        evidence_refs=[ref],
                    )
                )
            ids.append(ident)
        return ids

    for step in recipe["steps"]:
        number = step["display_step"]
        if step["kind"] == "说明":
            continue
        if step["kind"].startswith("可选") or step["kind"] == "条件分支":
            alternatives.append(
                {
                    "source_step": number,
                    "description": step["text"],
                    "timing_plan": step["timing_plan"],
                    "selected": False,
                }
            )
            continue
        plan = step["timing_plan"]
        seconds = plan["nominal_process_sec"]
        text = step["text"]
        if row == 98 and number == 5:
            text = "75克油加热，加入25克蒜末，小火炒至淡黄色后盛出。"
        if row == 98 and number == 6:
            text = "将剩余25克生蒜末与原配方生抽、料酒、鸡精、白胡椒粉混匀成调味汁。"
        if row == 93 and number == 4:
            text = (
                "将全部油皮面团和全部油酥各均分28份，枣泥总量按原配方20克均分28份；"
                "替代原每份38克/16克不一致口径。"
            )
        if row == 96:
            text = text.replace("羽衣甘蓝", "紫甘蓝")
        kind = step["kind"]
        key = (row, number)
        if key in CUSTOM:
            pieces = copy.deepcopy(CUSTOM[key])
        elif kind in ("等待", "可选等待"):
            device = device_for(step, row)
            if row in (64, 70, 74) and "发酵" in text:
                device = None
                seconds = {
                    64: step["source_time_constraint"]["upper_sec"],
                    70: 3600,
                    74: 3600 if number == 12 else 2400,
                }[row]
            verb = (
                "FREEZE"
                if device == "freezer"
                else "MARINATE"
                if "腌" in text
                else "CHILL"
                if device == "cold"
                else "WAIT"
            )
            pieces = [(verb, text, seconds, device, False)]
        elif kind == "人工":
            manual_device = None
            if re.search(r"装入|放入|取出|放回|烤箱门|关门", text) and re.search(
                r"烤箱|蒸箱|蒸烤.*机|机器|设备|腔体", text
            ):
                manual_device = device_for(step, row)
            pieces = [(action_for(text), text, seconds, manual_device, True)]
        elif kind == "中途介入":
            pieces = [("STIR", text, seconds, "oven", True)]
        else:
            device = device_for(step, row)
            pieces = []
            if key == (66, 4):
                seconds = 90
                text = "顶部烤230℃烤鱿鱼干90秒（原240℃60秒的AI替代）"
            elif key == (11, 8):
                text = "继续普通蒸100℃2分钟（原微波中高火2分钟的AI替代）"
            elif key == (41, 12):
                text = "上下火统一170℃烤35分钟（原180/165℃的AI替代）"
            if device in ("steam", "oven"):
                if key in ((91, 20), (77, 7)):
                    pieces = [("PREHEAT", text, seconds, device, False)]
                    initialized_devices.add(device)
                else:
                    if re.search(r"选择|设置|开启电源|开始烹饪", text):
                        pieces.append(
                            ("PREPARE", "设置设备程序/补水；" + recipe["name"], 60, device, True)
                        )
                    if device not in initialized_devices and "无需预热" not in text:
                        pieces.append(
                            (
                                "PREHEAT",
                                "预热/升温；" + recipe["name"],
                                300 if device == "steam" or key == (75, 10) else 600,
                                device,
                                False,
                            )
                        )
                        initialized_devices.add(device)
                    if re.search(r"放入|入机器|装入", text):
                        pieces.append(("LOAD", "装入本步骤食材", 60, device, True))
                    pieces.append(("HEAT", text, seconds, device, False))
                    if re.search(r"结束后.*取出|取出.*食用|取出即可", text):
                        pieces.append(("UNLOAD", "取出本步骤熟食", 60, device, True))
            elif device == "stove" and seconds > 600 and re.search(r"煮|炖", text):
                remaining = seconds
                while remaining:
                    segment = min(600, remaining)
                    if segment > 30:
                        pieces.append(("HEAT", text, segment - 30, device, False))
                        pieces.append(("STIR", "检查火力及原文要求的翻拌", 30, device, True))
                    else:
                        pieces.append(("HEAT", text, segment, device, True))
                    remaining -= segment
            else:
                pieces = [("HEAT", text, seconds, device, True)]
        groups[number] = emit(number, pieces, plan["basis"], step["source_time_constraint"])

    valid = set(groups)
    edges = source_edges(recipe, valid, mapping)
    for a, b in edges:
        dependencies.append(
            dict(
                predecessor_id=groups[a][-1],
                successor_id=groups[b][0],
                reason="源工序依赖；明确并行分支保留",
                evidence_refs=[ref],
            )
        )
    for extra, (target, pieces) in enumerate(PREPARATION.get(row, []), 1000):
        ids = emit(extra, pieces, "补全源文本中省略的必需前置；时间/未给用量为AI建议")
        dependencies.append(
            dict(
                predecessor_id=ids[-1],
                successor_id=groups[target][0],
                reason="前置食材必须准备完成",
                evidence_refs=[ref],
            )
        )
        groups[extra] = ids
    pending_extra_deps = []
    for prep in material_map.get("extra_preparation", []):
        prefix = re.match(r"^(100[0-9])：", prep["description"])
        number = prep.get("step") or (int(prefix[1]) if prefix else None)
        if number is None:
            number = next(n for n in range(1000, 1100) if n not in groups)
        if number in groups:
            continue
        description = re.sub(r"^100[0-9]：", "", prep["description"])
        seconds = prep["suggested_duration_sec"]
        device = prep.get("device")
        passive = not device and bool(re.search(r"放凉|降至|软化|回软|放至", description))
        action = "HEAT" if device == "stove" else "WAIT" if passive else action_for(description)
        pieces = [(action, description, seconds, device, not passive)]
        ids = emit(number, pieces, "逐菜物料追踪补出的缺失工序；参数为AI建议")
        groups[number] = ids
        before, after = prep.get("before_step"), prep.get("after_step")
        if before == 999:
            pending_extra_deps.append(ids[-1])
        elif before is not None:
            dependencies.append(
                dict(
                    predecessor_id=ids[-1],
                    successor_id=groups[before][0],
                    reason="缺失备料先于使用",
                    evidence_refs=[ref],
                )
            )
        if after is not None:
            dependencies.append(
                dict(
                    predecessor_id=groups[after][-1],
                    successor_id=ids[0],
                    reason="补充工序等待原工序完成",
                    evidence_refs=[ref],
                )
            )
    # 按真实装料/卸料划分设备会话；不能把离炉冷却和冷藏锁在腔体内。
    operation_by_id = {op["operation_id"]: op for op in ops}
    parents = {ident: [] for ident in operation_by_id}
    for dep in dependencies:
        parents[dep["successor_id"]].append(dep["predecessor_id"])

    def food_text(op: dict) -> str:
        return re.sub(r"取出水箱(?:加满水)?", "", op["description"])

    def releases(op: dict) -> bool:
        body = food_text(op)
        if re.search(r"放回|回装|再.*装入|第二批.*装入", body):
            return False
        return op["action"] == "UNLOAD" or bool(
            re.search(r"取出|盛出|捞出|出锅|关火.*淋|关火.*倒入", body)
        )

    def closest_device(ident: str) -> str | None:
        frontier, visited = list(parents[ident]), set()
        while frontier:
            candidates = {op_device[p] for p in frontier if p in op_device}
            candidates -= {"cold", "freezer"}
            if len(candidates) == 1:
                return next(iter(candidates))
            if candidates:
                return None
            visited.update(frontier)
            frontier = [p for q in frontier for p in parents[q] if p not in visited]
        return None

    for op in ops:
        ident, body = op["operation_id"], food_text(op)
        if ident in op_device or not any(
            u["resource_type"] == "HUMAN" for u in op["resource_requirements"]
        ):
            continue
        device = None
        step_number = metadata[ident]["source_step"]
        if (row == 75 and step_number in range(8, 14)) or (
            row == 91 and step_number in range(19, 26)
        ):
            device = "oven"
        elif re.search(r"放.*蒸箱|入.*蒸箱|取出蒸熟|蒸好.*取出", body):
            device = "steam"
        elif re.search(r"放.*烤箱|入.*烤箱|从烤盘.*取出", body):
            device = "oven"
        elif releases(op) and not re.search(r"冰箱|冷藏|冷冻|料理台", body):
            device = closest_device(ident)
        elif re.search(r"锅中|锅内|入炒锅|出锅", body):
            device = "stove"
        if device:
            op_device[ident] = device
            op["resource_requirements"].append(resource_use(device, row, body, ref))

    sessions = []
    for device in ("stove", "steam", "oven"):
        members = [op["operation_id"] for op in ops if op_device.get(op["operation_id"]) == device]
        connected = {ident: set() for ident in members}
        for dep in dependencies:
            a, b = dep["predecessor_id"], dep["successor_id"]
            if a in connected and b in connected and not releases(operation_by_id[a]):
                connected[a].add(b)
                connected[b].add(a)
        # 固定套餐的开门介入不释放腔体，整个程序仍为单一预约。
        if row in (75, 91) and device == "oven" and members:
            for ident in members[1:]:
                connected[members[0]].add(ident)
                connected[ident].add(members[0])
        seen = set()
        for ident in members:
            if ident in seen:
                continue
            stack, component = [ident], set()
            while stack:
                current = stack.pop()
                if current in component:
                    continue
                component.add(current)
                stack.extend(connected[current] - component)
            seen.update(component)
            ordered = [member for member in members if member in component]
            terminals = component - {
                d["predecessor_id"]
                for d in dependencies
                if d["predecessor_id"] in component and d["successor_id"] in component
            }
            holds_food = any(
                operation_by_id[member]["action"] not in ("PREPARE", "PREHEAT")
                for member in component
            )
            if (
                terminals
                and holds_food
                and not all(releases(operation_by_id[end]) for end in terminals)
            ):
                number = 8000 + len(sessions)
                release = emit(
                    number,
                    [("UNLOAD", "本次加工结束，移出食物并释放设备", 60, device, True)],
                    "局部加工结束即卸料；不等待其他分支、冷藏或最终出菜",
                )[0]
                for dep in dependencies:
                    if dep["predecessor_id"] in terminals and dep["successor_id"] not in component:
                        dep["predecessor_id"] = release
                for end in sorted(terminals):
                    dependencies.append(
                        dict(
                            predecessor_id=end,
                            successor_id=release,
                            reason="本次加工后卸料释放",
                            evidence_refs=[ref],
                        )
                    )
                terminal_groups = {
                    number for number, group in groups.items() if any(t in group for t in terminals)
                }
                if len(terminal_groups) == 1:
                    group_number = terminal_groups.pop()
                    group = groups[group_number]
                    insertion = max(group.index(end) for end in terminals) + 1
                    group.insert(insertion, release)
                    metadata[release]["source_step"] = group_number
                else:
                    metadata[release]["release_after_operations"] = sorted(terminals)
                ordered.append(release)
            sessions.append((device, ordered))

    outgoing = {d["predecessor_id"] for d in dependencies}
    tails = [op["operation_id"] for op in ops if op["operation_id"] not in outgoing]
    cleanup = []
    cleanup.append(("FINISH", "确认全部必需分支完成并装盘上桌", 60, None, True))
    finishing = emit(999, cleanup, "补齐设备停止、卸料和最终出菜边界")
    groups[999] = finishing
    for predecessor in pending_extra_deps:
        dependencies.append(
            dict(
                predecessor_id=predecessor,
                successor_id=finishing[0],
                reason="新增收尾先于最终出菜",
                evidence_refs=[ref],
            )
        )
    for tail in tails:
        dependencies.append(
            dict(
                predecessor_id=tail,
                successor_id=finishing[0],
                reason="全部必需分支完成后才可出菜",
                evidence_refs=[ref],
            )
        )

    # 原子释放可能插在同一源步骤中间；按真实依赖排序，不能让物料装配再造反向边。
    from graphlib import TopologicalSorter

    group_graph = {op["operation_id"]: set() for op in ops}
    for dep in dependencies:
        group_graph[dep["successor_id"]].add(dep["predecessor_id"])
    topological_rank = {
        ident: rank for rank, ident in enumerate(TopologicalSorter(group_graph).static_order())
    }
    for group in groups.values():
        group.sort(key=topological_rank.__getitem__)

    if row in (75, 91):
        before, event, after = (10, 11, 12) if row == 75 else (22, 23, 24)
        values = (1800, 120, 900) if row == 75 else (1200, 240, 900)
        constraints.append(
            dict(
                program_id=f"{rid}:fixed_program",
                before_group=groups[before],
                intervention_group=groups[event],
                after_group=groups[after],
                before_intervention_sec=values[0],
                intervention_sec=values[1],
                remaining_sec=values[2],
                active_process_sec=values[0] + values[2],
                timer_paused=True,
                origin="MODEL_SUGGESTION",
                trigger_type="remaining_time" if row == 75 else "elapsed_time",
                trigger_value=900 if row == 75 else 1200,
            )
        )
        # 从前段有效加热结束到介入、再到末段开始均不允许插入任意等待。
        for a, b in [(groups[before][-1], groups[event][0]), (groups[event][-1], groups[after][0])]:
            for dep in dependencies:
                if (dep["predecessor_id"], dep["successor_id"]) == (a, b):
                    dep["max_lag_sec"] = 0
        # 介入点同时写入契约 offset，锚定前段有效HEAT起点。
        parent = next(
            op
            for op in reversed(ops)
            if op["operation_id"] in groups[before] and op["action"] == "HEAT"
        )
        parent["execution_policy"]["interventions"] = [
            {
                "operation_id": groups[event][0],
                "offset_min_sec": values[0],
                "offset_max_sec": values[0],
            }
        ]

    # 每个实际设备会话独立预约；其他菜可利用卸料后的空档。
    for index, (device, members) in enumerate(sessions):
        reservations.append(
            dict(
                reservation_id=f"{rid}:{device}:{index}",
                members=members,
                resource_options=["burner_1", "burner_2"]
                if device == "stove"
                else ["steam_oven_1" if device == "steam" else "oven_1"],
                policy="UNARY",
                span="min_start_to_max_end",
                origin="MODEL_SUGGESTION",
            )
        )
    # 固定原配方物料清单和操作状态关联。共享/克重流转不由文本推断。
    incoming = {op["operation_id"]: [] for op in ops}
    for dep in dependencies:
        incoming[dep["successor_id"]].append(dep["predecessor_id"])
    descriptions = {op["operation_id"]: op["description"] for op in ops}
    for ident, meta in metadata.items():
        meta["predecessor_states"] = [metadata[p]["output_state"] for p in incoming[ident]]
        meta["input_state"] = (
            "；".join(meta["predecessor_states"]) or "原配方指定状态的原料/空容器可用"
        )
        meta["output_state"] = "完成操作：" + descriptions[ident]
        meta["ingredient_refs"] = [
            spec["spec_id"]
            for spec in specs
            if spec["name"] and spec["name"] in descriptions[ident]
        ]
    canonical = CanonicalRecipeModel.model_validate(
        dict(
            schema_version="1.0",
            recipe_id=rid,
            name=recipe["name"],
            recipe_version=VERSION,
            ingredient_requirements=requirements,
            material_specs=specs,
            operations=ops,
            dependencies=dependencies,
            provenance_refs=[ref],
            review_status="NEEDS_REVIEW",
        )
    ).model_dump(mode="json")
    return dict(
        recipe_id=rid,
        name=recipe["name"],
        source_record=recipe["original_record"],
        revised_source_record=recipe["revised_input_record"],
        canonical=canonical,
        operation_metadata=metadata,
        source_dependencies=edges,
        source_groups=groups,
        resource_reservations=reservations,
        program_constraints=constraints,
        alternative_paths=alternatives,
        adaptations=ADAPTATIONS.get(row, []),
        ingredient_supplements=SUPPLEMENTS.get(row, []),
        review_notes=recipe["notes"] + [s["text"] for s in recipe["steps"] if s["kind"] == "说明"],
        material_policy="固定原配方整批；禁止跨菜物料共享；批次状态保留于operation_metadata",
        evidence=evidence,
    )


def main() -> None:
    data = json.loads(INPUT.read_text(encoding="utf-8"))
    mapping = json.loads((ROOT / "data/issues/p0_process_map.json").read_text(encoding="utf-8"))
    ingredient_records = [effective_record(r, i) for i, r in enumerate(data["recipes"], 1)]
    (OUT / "ingredient_inputs.json").write_text(
        json.dumps(
            {"origin": "MODEL_SUGGESTION", "records": ingredient_records},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    recipes = [convert(r, i, mapping) for i, r in enumerate(data["recipes"], 1)]
    from scripts.attach_recipe_materials import attach_materials

    material_files = sorted(OUT.glob("material_map_*.json"))
    maps = {
        e["recipe_id"]: (e, f)
        for f in material_files
        for e in json.loads(f.read_text(encoding="utf-8"))
    }
    for recipe in recipes:
        entry, path = maps[recipe["recipe_id"]]
        try:
            attach_materials(recipe, entry)
        except ValueError as exc:
            raise ValueError(f"菜谱{entry['row']} {recipe['name']}: {exc}") from exc
        recipe["canonical"] = CanonicalRecipeModel.model_validate(recipe["canonical"]).model_dump(
            mode="json"
        )
        specs_by_id = {s["spec_id"]: s for s in recipe["canonical"]["material_specs"]}
        for op in recipe["canonical"]["operations"]:
            meta = recipe["operation_metadata"][op["operation_id"]]
            meta["input_state"] = (
                "；".join(specs_by_id[m["spec_id"]]["state"] for m in op["material_inputs"])
                or "设备/工艺辅助操作，无食物投入"
            )
            meta["output_state"] = (
                "；".join(specs_by_id[m["spec_id"]]["state"] for m in op["material_outputs"])
                or "设备/工艺辅助操作完成"
            )
        if entry["row"] == 68:
            recipe["adaptations"].append(
                "原文重复列出两套不同配方；AI默认仅选首组A料作为一批，后组主料不叠加。原文两组完整保留。"
            )
        recipe["material_policy"] = (
            "逐食材及半成品追踪；固定原配方整批；未称重产物为定性批次；禁止跨菜共享"
        )
        recipe["material_mapping_file"] = path.relative_to(ROOT).as_posix()

    resources = [
        {
            "resource_id": "human_1",
            "physical_resource_id": "human_1",
            "component_id": "person",
            "capacity": 1,
            "policy": "UNARY",
        },
        {
            "resource_id": "burner_1",
            "physical_resource_id": "stove_1",
            "component_id": "burner_1",
            "capacity": 1,
            "policy": "UNARY",
        },
        {
            "resource_id": "burner_2",
            "physical_resource_id": "stove_1",
            "component_id": "burner_2",
            "capacity": 1,
            "policy": "UNARY",
        },
        {
            "resource_id": "steam_oven_1",
            "physical_resource_id": "steam_oven_1",
            "component_id": "chamber",
            "capacity": 1,
            "policy": "UNARY",
        },
        {
            "resource_id": "oven_1",
            "physical_resource_id": "oven_1",
            "component_id": "chamber",
            "capacity": 1,
            "policy": "UNARY",
        },
        {
            "resource_id": "fridge_cold_1",
            "physical_resource_id": "fridge_1",
            "component_id": "cold_zone",
            "policy": "STATE_COMPATIBLE",
            "temperature_c": 4,
        },
        {
            "resource_id": "fridge_freezer_1",
            "physical_resource_id": "fridge_1",
            "component_id": "freezer_zone",
            "policy": "STATE_COMPATIBLE",
            "temperature_c": -18,
        },
    ]
    result = {
        "schema_version": "development-scheduling-1",
        "knowledge_version": VERSION,
        "status": "MODEL_SUGGESTION",
        "review_status": "NEEDS_REVIEW",
        "approved_count": 0,
        "resources": resources,
        "recipes": recipes,
        "device_assumptions": [
            "依据清单分列蒸箱和烤箱，按一台蒸箱一台烤箱建立开发物理实例；灶具两灶眼。",
            "同一设备的多个模式共用同一腔体；加湿烤套餐不另造设备。",
            "冷藏与冷冻共享各温区，按4℃和-18℃同状态兼容，不虚构托盘/容积容量。",
            "刀、砧板、锅盘作为工艺工具说明，未擅自新增数量约束；灶上锅与该灶位连续占用绑定。",
        ],
        "execution_policy": [
            "本包适用于用户授权的AI估计开发排程，未经人工工艺批准。",
            "必需路径选择固定，备选冷冻/微波/口感等待不同时累计。",
            "资源预约含介入和批次间隙；主动人工仅human_1。",
            "固定原配方一批，禁用跨菜共享切配或共批；数量转换、共享物料账留给正式领域流程。",
        ],
        "source_hashes": {
            "docs/recipes_100_详细步骤_完善版.csv": sha(
                ROOT / "docs/recipes_100_详细步骤_完善版.csv"
            ),
            "docs/问题/数据问题.docx": sha(ROOT / "docs/问题/数据问题.docx"),
            "docs/设备参数清单参考.json": sha(ROOT / "docs/设备参数清单参考.json"),
            INPUT.relative_to(ROOT).as_posix(): sha(INPUT),
        },
        "provenance": [
            {
                "provenance_id": f"ai-timing:{r['recipe_id']}",
                "origin": "MODEL_SUGGESTION",
                "source_file": INPUT.relative_to(ROOT).as_posix(),
                "source_hash": sha(INPUT),
                "record_id": r["recipe_id"],
                "field_path": "/recipes",
                "text_span": "用户授权AI补齐时间与替代工艺；各步骤保留原文和估计依据。",
                "review_status": "NEEDS_REVIEW",
            }
            for r in recipes
        ],
    }
    result["source_hashes"].update(
        {
            f.relative_to(ROOT).as_posix(): sha(f)
            for f in [
                *material_files,
                OUT / "ingredient_inputs.json",
                Path(__file__),
                ROOT / "scripts/attach_recipe_materials.py",
            ]
        }
    )
    for recipe in recipes:
        path = ROOT / recipe["material_mapping_file"]
        result["provenance"].append(
            dict(
                provenance_id=f"ai-material:{recipe['recipe_id']}",
                origin="MODEL_SUGGESTION",
                source_file=path.relative_to(ROOT).as_posix(),
                source_hash=sha(path),
                record_id=recipe["recipe_id"],
                field_path="/stages",
                text_span="逐菜原料份额、半成品与必需前置；用户授权AI补全，未作人工批准",
                review_status="NEEDS_REVIEW",
            )
        )
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "scheduling_dataset.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    rows = []
    for recipe in recipes:
        model = recipe["canonical"]
        preds = {op["operation_id"]: [] for op in model["operations"]}
        for dep in model["dependencies"]:
            preds[dep["successor_id"]].append(dep["predecessor_id"])
        parts = []
        for i, op in enumerate(model["operations"], 1):
            refs = ",".join(u["resource_id"] for u in op["resource_requirements"]) or "无独占资源"
            values = ",".join(
                f"{c['parameter']}={c['value']}"
                for u in op["resource_requirements"]
                for c in u["configuration"]
            )
            description = re.sub(r"第(\d+)步", r"原始步骤\1", op["description"])
            parts.append(
                f"第{i}步【id={op['operation_id']}｜时间={op['duration']['execution_sec']}秒"
                f"；资源={refs}；前置={','.join(preds[op['operation_id']]) or '无'}"
                f"；参数={values or '不适用'}；来源=原文及AI估计】{description}"
                f"；投入={','.join(m['spec_id'] for m in op['material_inputs']) or '无食物投入'}"
                f"；产出={','.join(m['spec_id'] for m in op['material_outputs']) or '无食物产出'}"
            )
        row = dict(ingredient_records[recipes.index(recipe)])
        row["烹饪步骤"] = "\n".join(parts)
        rows.append(row)
    (OUT / "csv_payload.json").write_text(
        json.dumps(
            {
                "headers": data["headers"],
                "rows": rows,
                "source_rows": json.loads(
                    (ROOT / "data/revisions/recipes_v2/recipes_100_完善版.json").read_text(
                        encoding="utf-8"
                    )
                )["rows"],
                "source_path": "docs/recipes_100_详细步骤_完善版.csv",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "recipes": len(recipes),
                "operations": sum(len(r["canonical"]["operations"]) for r in recipes),
                "approved": 0,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
