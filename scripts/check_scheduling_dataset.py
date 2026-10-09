"""独立检查开发数据并用CP-SAT验证固定路径可调度性；不替代P2完整求解器。"""

from __future__ import annotations

import hashlib
import json
import random
import re
from collections import defaultdict
from fractions import Fraction
from graphlib import TopologicalSorter
from pathlib import Path

from ortools.sat.python import cp_model

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "data/revisions/recipes_v3/scheduling_dataset.json"


def check_materials(recipe: dict) -> None:
    """独立读取实际canonical物料，而非信任装配器material_flow计数。"""
    model = recipe["canonical"]
    supplies = {r["spec_id"]: r for r in model["ingredient_requirements"]}
    producers = {}
    parents = {op["operation_id"]: set() for op in model["operations"]}
    for edge in model["dependencies"]:
        parents[edge["successor_id"]].add(edge["predecessor_id"])
    ancestors = {}
    for ident in TopologicalSorter(parents).static_order():
        ancestors[ident] = set(parents[ident])
        for parent in parents[ident]:
            ancestors[ident].update(ancestors[parent])
    for op in model["operations"]:
        for requirement in op["material_outputs"]:
            key = requirement["spec_id"]
            assert key not in supplies and key not in producers, "物料重复生产"
            producers[key] = op["operation_id"]
            supplies[key] = requirement
    consumed = defaultdict(Fraction)
    for op in model["operations"]:
        for requirement in op["material_inputs"]:
            key = requirement["spec_id"]
            assert key in supplies, "悬空物料"
            if key in producers:
                assert producers[key] in ancestors[op["operation_id"]], "物料生产者缺少前置路径"
            supply = supplies[key]
            if requirement["quantity_kind"] in ("EXACT", "RANGE"):
                q, total = requirement["quantity"], supply["quantity"]
                assert q["unit"] == total["unit"], "物料单位不一致"
                share = Fraction(q["value"], q["scale"]) / Fraction(total["value"], total["scale"])
                if requirement["quantity_kind"] == "RANGE":
                    high, bound = requirement["upper_quantity"], supply["upper_quantity"]
                    assert Fraction(high["value"], high["scale"]) == share * Fraction(
                        bound["value"], bound["scale"]
                    )
            else:
                match = re.search(
                    r"(?:原配方份额|本物料整批的)([0-9/]+)", requirement["qualitative_quantity"]
                )
                assert match, "物料定性份额缺失"
                share = Fraction(match[1])
            assert share > 0
            consumed[key] += share
            assert consumed[key] <= 1, f"物料超用：{key}"
    assert "finaldish" in producers, "缺少真实成品"
    raw = {r["spec_id"] for r in model["ingredient_requirements"]}
    assert all(consumed[key] == 1 for key in raw), "原料存在未声明剩余"
    for spec in model["material_specs"]:
        assert set(spec["composition"]) <= raw, "物料谱系引用非原料"


def check_data(data: dict) -> None:
    assert len(data["recipes"]) == 100
    assert len({r["recipe_id"] for r in data["recipes"]}) == 100
    profiles = {
        "上下火": (60, 230),
        "全开烤": (60, 230),
        "鼓风烤": (60, 230),
        "顶部烤": (60, 230),
        "加湿烤": (60, 230),
        "环风烤": (60, 230),
        "蔬果干": (60, 120),
        "鲜嫩蒸": (30, 90),
        "普通蒸": (91, 100),
    }
    for recipe in data["recipes"]:
        check_materials(recipe)
        nodes = recipe["canonical"]["operations"]
        ids = {op["operation_id"] for op in nodes}
        graph = {key: set() for key in ids}
        for dep in recipe["canonical"]["dependencies"]:
            assert dep["predecessor_id"] in ids and dep["successor_id"] in ids
            graph[dep["successor_id"]].add(dep["predecessor_id"])
        assert len(list(TopologicalSorter(graph).static_order())) == len(ids)
        assert len(ids) == len(nodes)
        for op in nodes:
            assert (
                isinstance(op["duration"]["execution_sec"], int)
                and op["duration"]["execution_sec"] > 0
            )
            for u in op["resource_requirements"]:
                if u["resource_type"] == "HUMAN":
                    assert u["resource_id"] == "human_1" and u["units"] == 1
                values = {v["parameter"]: v["value"] for v in u["configuration"]}
                if "power_level" in values:
                    assert 1 <= values["power_level"] <= 7
                if "mode" in values and values["mode"] in profiles:
                    lower, upper = profiles[values["mode"]]
                    assert lower <= values["temperature_c"] <= upper, (
                        recipe["name"],
                        op["operation_id"],
                        values,
                    )
                if u["resource_id"] == "fridge_cold_1":
                    assert values["temperature_c"] == 4
                if u["resource_id"] == "fridge_freezer_1":
                    assert values["temperature_c"] == -18
        reserved = {member for r in recipe["resource_reservations"] for member in r["members"]}
        device_ops = {
            op["operation_id"]
            for op in nodes
            if any(
                u["resource_type"] == "DEVICE"
                and u["resource_id"] not in ("fridge_cold_1", "fridge_freezer_1")
                for u in op["resource_requirements"]
            )
        }
        assert device_ops <= reserved


def validate_schedule(recipes: list[dict], plan: dict) -> None:
    """逐项重算时长、依赖、人工、设备和介入；不调用求解约束生成代码。"""
    expected = {
        (r["recipe_id"], op["operation_id"]): op
        for r in recipes
        for op in r["canonical"]["operations"]
    }
    actual = {(t["recipe_id"], t["operation_id"]): t for t in plan["tasks"]}
    assert set(actual) == set(expected) and len(actual) == len(plan["tasks"]), "任务覆盖不完整/重复"
    human = []
    device_spans = defaultdict(list)
    for key, op in expected.items():
        t = actual[key]
        assert isinstance(t["start_sec"], int) and t["start_sec"] >= 0
        assert t["end_sec"] - t["start_sec"] == op["duration"]["execution_sec"], "时长不匹配"
        if any(u["resource_type"] == "HUMAN" for u in op["resource_requirements"]):
            human.append((t["start_sec"], t["end_sec"], key))
    human.sort()
    assert all(a[1] <= b[0] for a, b in zip(human, human[1:], strict=False)), "人工重叠"
    for recipe in recipes:
        rid = recipe["recipe_id"]
        for dep in recipe["canonical"]["dependencies"]:
            first = actual[(rid, dep["predecessor_id"])]
            second = actual[(rid, dep["successor_id"])]
            lag = second["start_sec"] - first["end_sec"]
            assert lag >= dep["min_lag_sec"], "前置未完成"
            if dep["max_lag_sec"] is not None:
                assert lag <= dep["max_lag_sec"], "连续流程被插入等待"
        for op in recipe["canonical"]["operations"]:
            for event in op["execution_policy"]["interventions"]:
                delta = (
                    actual[(rid, event["operation_id"])]["start_sec"]
                    - actual[(rid, op["operation_id"])]["start_sec"]
                )
                assert event["offset_min_sec"] <= delta <= event["offset_max_sec"], "介入时点错误"
        for r in recipe["resource_reservations"]:
            assignment = plan["resource_assignments"][r["reservation_id"]]
            assert assignment in r["resource_options"], "资源选择不合法"
            values = [actual[(rid, member)] for member in r["members"]]
            start = min(t["start_sec"] for t in values)
            end = max(t["end_sec"] for t in values)
            device_spans[assignment].append((start, end, r["reservation_id"]))
    for resource, spans in device_spans.items():
        spans.sort()
        assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:], strict=False)), (
            f"设备重叠：{resource}"
        )


def solve(recipes: list[dict]) -> dict:
    model = cp_model.CpModel()
    horizon = (
        sum(op["duration"]["execution_sec"] for r in recipes for op in r["canonical"]["operations"])
        + 1
    )
    starts, ends = {}, {}
    human = []
    calendars = defaultdict(list)
    choices = {}
    for recipe in recipes:
        rid = recipe["recipe_id"]
        for op in recipe["canonical"]["operations"]:
            key = (rid, op["operation_id"])
            start = model.new_int_var(0, horizon, "s:" + ":".join(key))
            end = model.new_int_var(0, horizon, "e:" + ":".join(key))
            interval = model.new_interval_var(
                start, op["duration"]["execution_sec"], end, "i:" + ":".join(key)
            )
            starts[key], ends[key] = start, end
            if any(u["resource_type"] == "HUMAN" for u in op["resource_requirements"]):
                human.append(interval)
        for dep in recipe["canonical"]["dependencies"]:
            a, b = (rid, dep["predecessor_id"]), (rid, dep["successor_id"])
            model.add(starts[b] >= ends[a] + dep["min_lag_sec"])
            if dep["max_lag_sec"] is not None:
                model.add(starts[b] <= ends[a] + dep["max_lag_sec"])
        for op in recipe["canonical"]["operations"]:
            for event in op["execution_policy"]["interventions"]:
                offset = starts[(rid, event["operation_id"])] - starts[(rid, op["operation_id"])]
                model.add(offset >= event["offset_min_sec"])
                model.add(offset <= event["offset_max_sec"])
        for r in recipe["resource_reservations"]:
            start = model.new_int_var(0, horizon, r["reservation_id"] + ":s")
            end = model.new_int_var(0, horizon, r["reservation_id"] + ":e")
            size = model.new_int_var(1, horizon, r["reservation_id"] + ":d")
            model.add_min_equality(start, [starts[(rid, m)] for m in r["members"]])
            model.add_max_equality(end, [ends[(rid, m)] for m in r["members"]])
            model.add(size == end - start)
            flags = []
            for resource in r["resource_options"]:
                present = model.new_bool_var(r["reservation_id"] + ":" + resource)
                interval = model.new_optional_interval_var(
                    start, size, end, present, r["reservation_id"] + ":" + resource
                )
                calendars[resource].append(interval)
                flags.append(present)
                choices[(r["reservation_id"], resource)] = present
            model.add_exactly_one(flags)
    model.add_no_overlap(human)
    for intervals in calendars.values():
        model.add_no_overlap(intervals)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 5
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 42
    status = solver.solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return {"status": solver.status_name(status), "tasks": []}
    plan = {
        "status": solver.status_name(status),
        "tasks": [
            {
                "recipe_id": key[0],
                "operation_id": key[1],
                "start_sec": solver.value(start),
                "end_sec": solver.value(ends[key]),
            }
            for key, start in starts.items()
        ],
        "resource_assignments": {
            res: resource for (res, resource), flag in choices.items() if solver.value(flag)
        },
        "solve_wall_sec": solver.wall_time,
        "optimality_claim": False,
    }
    validate_schedule(recipes, plan)
    return plan


def main() -> None:
    data = json.loads(PATH.read_text(encoding="utf-8"))
    check_data(data)
    recipes = data["recipes"]
    reports = []
    plans = []
    selected = [("single-" + str(i + 1), [r]) for i, r in enumerate(recipes)]
    selected += [(f"coverage-menu-{i // 5 + 1}", recipes[i : i + 5]) for i in range(0, 100, 5)]
    for i, indices in enumerate([[1, 10, 52], [16, 88, 98], [75, 91], [87, 98, 85], [3, 44, 67]]):
        selected.append((f"boundary-menu-{i + 1}", [recipes[n - 1] for n in indices]))
    rng = random.Random(42)
    selected += [
        (f"random-menu-{i + 1}", rng.sample(recipes, rng.randint(3, 5))) for i in range(20)
    ]
    for label, menu in selected:
        plan = solve(menu)
        passed = bool(plan["tasks"])
        reports.append(
            {
                "case": label,
                "recipe_ids": [r["recipe_id"] for r in menu],
                "status": plan["status"],
                "independent_validation": passed,
            }
        )
        if not passed:
            print(json.dumps(reports[-1], ensure_ascii=False), flush=True)
        if label.startswith("boundary"):
            plans.append({"case": label, **plan})
    report = {
        "dataset_sha256": hashlib.sha256(PATH.read_bytes()).hexdigest(),
        "scope": "开发数据固定路径可调度性；不代替工艺实测、人工审核、正式P1/P2验收或最优性证明",
        "cases": reports,
        "passed": sum(r["independent_validation"] for r in reports),
        "total": len(reports),
        "all_passed": all(r["independent_validation"] for r in reports),
    }
    (PATH.parent / "scheduling_verification.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (PATH.parent / "sample_schedules.json").write_text(
        json.dumps(plans, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({k: report[k] for k in ("passed", "total", "all_passed")}, ensure_ascii=False))
    if not report["all_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
