"""固定发布上的全量评估清单；开发输入始终保留 development 身份。"""

import argparse
import hashlib
import json
import random
from collections import Counter
from pathlib import Path

from app.config import AppSettings
from app.domain.base import content_hash
from app.domain.policy import SchedulingPolicy
from app.knowledge.loader import read_release_ref
from app.knowledge.repository import SnapshotKnowledgeRepository

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "2026-10-04T10:00:00+08:00"


def load_inputs(settings=None):
    # 既有评估清单绑定 P4 策略；新策略实验必须显式传入设置，不能
    # 因在线默认值变化而把历史清单与新的策略身份混在一起。
    settings = settings or AppSettings(policy_path=ROOT / "data/policies/p4-runtime-v1.json")
    reference = read_release_ref(settings.release_root, settings.release_id)
    repository = SnapshotKnowledgeRepository(settings.release_root)
    repository.load(reference)
    with repository.acquire(reference) as lease:
        knowledge = lease.select(
            tuple(r.recipe_id for r in lease.loaded.snapshot.knowledge.recipes)
        )
    policy = SchedulingPolicy.model_validate_json(settings.policy_path.read_bytes())
    return knowledge, policy


def suite_hash(suite):
    encoded = json.dumps(
        {k: v for k, v in suite.items() if k != "suite_hash"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def build_suite(knowledge, policy, seed):
    recipes = sorted(knowledge.recipes, key=lambda r: r.recipe_id.root)
    ids = [r.recipe_id.root for r in recipes]
    if len(ids) != 100 or len(set(ids)) != 100 or knowledge.release.release_kind == "sample":
        raise ValueError("全量评估需要固定 100 个不同 ID，不能使用 sample 发布")
    randomizer = random.Random(seed)

    def case(kind, index, members, **extra):
        return {
            "case_id": f"{kind}-{index:03d}",
            "category": kind,
            "source_kind": "REAL_PUBLISHED_RECIPES_SYNTHETIC_MENU_AND_EVENTS",
            "recipe_ids": list(members),
            **extra,
        }

    combinations = []
    shuffled = list(ids)
    randomizer.shuffle(shuffled)
    for index in range(200):
        members = shuffled[index * 4 : index * 4 + 4] if index < 25 else []
        members = members or randomizer.sample(ids, randomizer.randint(3, 5))
        combinations.append(case("combination", index, members))
    names = {r.name: r.recipe_id.root for r in recipes}
    pairs = [
        ("双椒鳙鱼头", "豉汁蒸草鱼"),
        ("家常鲈鱼", "鸡翅包饭"),
        ("糯米烧麦", "轻松一锅蒸"),
        ("亲朋欢聚套餐", "烹香酷炒汇"),
        ("鸡仔饼", "广式叉烧"),
        ("低温牛排", "柠檬干"),
        ("香菇烤芦笋", "香烤排骨"),
        ("蒸茄龙", "蒜蓉粉丝烤茄子"),
        ("豆豉蒸腐竹", "豆豉蒸腐竹（台式）"),
        ("三味蒸鳗鱼&香茅烤鳗鱼同烹", "鸡翅包饭"),
        ("雪媚娘", "拿破仑酥"),
        ("抹茶红豆白玉卷", "鸡仔饼"),
        ("栗子冰皮月饼", "大理石饼干"),
        ("南瓜鸡腿煲", "秘制叉烧"),
        ("草莓蛋挞", "黄油小饼干"),
        ("葱香土豆泥", "血糯米仙草奶茶"),
        ("小碗蒸肉", "荷叶蒸蟹饭"),
        ("香辣烤小龙虾", "法式羊排"),
        ("本帮白切鸡", "竹荪排骨汤"),
    ]
    boundaries = [
        case("boundary", i, [names[a], names[b]], features=[a, b]) for i, (a, b) in enumerate(pairs)
    ]
    boundaries.append(
        case(
            "boundary",
            19,
            [r.recipe_id.root for r in recipes if r.name == "麻辣对虾"],
            features=["同名不同 ID"],
        )
    )
    replans = []
    for index in range(20):
        members = combinations[index]["recipe_ids"]
        kind = ("ADD_RECIPE", "DELAY_RECIPE", "CANCEL_RECIPE", "REPLAY_ADD")[index % 4]
        extra = next(i for i in ids if i not in members)
        replans.append(
            case(
                "replan",
                index,
                members,
                event_script={
                    "event_type": kind,
                    "additional_recipe_id": extra,
                    "target_recipe_index": 0,
                    "earliest_start_sec": 300 + index * 30,
                    "occurred_offset_sec": 0,
                },
            )
        )
    suite = {
        "schema_version": "1.0",
        "suite_id": "p6-full-v1",
        "seed": seed,
        "time_origin": ORIGIN,
        "release": knowledge.release.model_dump(mode="json"),
        "knowledge_hash": content_hash(knowledge),
        "policy_hash": content_hash(policy),
        "policy": policy.model_dump(mode="json"),
        "single_recipes": [case("single", i, [r.recipe_id.root]) for i, r in enumerate(recipes)],
        "combinations": combinations,
        "boundaries": boundaries,
        "replans": replans,
        "supplementary_regressions": {
            "device_matrix": "tests/unit/test_device_material_exceptions.py",
            "mandatory_batches": "tests/unit/test_joint_thermal_batches.py",
            "shared_leftovers": "tests/integration/test_shared_leftover_replanning.py",
            "running_batches": "tests/integration/test_running_thermal_batch.py",
        },
        "supplementary_scope": "合成设备边界和已执行共享/运行批次另跑登记的回归，不计为真实菜单",
        "name_collisions": dict(Counter(r.name for r in recipes)),
    }
    suite["suite_hash"] = suite_hash(suite)
    validate_suite(suite, knowledge, policy)
    return suite


def validate_suite(suite, knowledge, policy):
    if suite.get("suite_hash") != suite_hash(suite):
        raise ValueError("评估清单哈希不一致，修改后必须生成新的绑定证据")
    if suite["release"] != knowledge.release.model_dump(mode="json"):
        raise ValueError("评估清单不属于当前发布")
    if suite["knowledge_hash"] != content_hash(knowledge):
        raise ValueError("知识输入改变，不能复用旧结果")
    if suite["policy_hash"] != content_hash(policy):
        raise ValueError("策略改变，不能复用旧结果")
    ids = {r.recipe_id.root for r in knowledge.recipes}
    if len(ids) != 100 or knowledge.release.release_kind == "sample":
        raise ValueError("全量知识身份不符合约定")
    groups = (("single_recipes", 100), ("combinations", 200), ("boundaries", 20), ("replans", 20))
    all_cases = []
    for name, minimum in groups:
        cases = suite[name]
        if len(cases) < minimum:
            raise ValueError(f"{name} 数量不足")
        for item in cases:
            members = item["recipe_ids"]
            if not members or len(members) != len(set(members)) or not set(members) <= ids:
                raise ValueError("菜单存在空输入、重复或发布之外 ID")
            if name == "combinations" and not 3 <= len(members) <= 5:
                raise ValueError("组合菜单必须为 3 至 5 道")
        all_cases.extend(cases)
    if len({c["case_id"] for c in all_cases}) != len(all_cases):
        raise ValueError("评估场景身份重复")
    if {i for c in suite["combinations"] for i in c["recipe_ids"]} != ids:
        raise ValueError("组合集未覆盖全部 ID")
    if {c["recipe_ids"][0] for c in suite["single_recipes"]} != ids:
        raise ValueError("单菜集未覆盖全部 ID")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=20261004)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "benchmarks/scenarios/full_suite.json"
    )
    args = parser.parse_args()
    knowledge, policy = load_inputs()
    suite = build_suite(knowledge, policy, args.seed)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        existing = json.loads(args.output.read_bytes())
        if existing != suite:
            raise FileExistsError("既有清单不能覆盖；请指定新的输出路径")
    else:
        args.output.write_text(
            json.dumps(suite, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(
        json.dumps(
            {
                "suite_hash": suite["suite_hash"],
                "scope": knowledge.release.release_kind,
                "counts": {
                    k: len(suite[k])
                    for k in ("single_recipes", "combinations", "boundaries", "replans")
                },
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
