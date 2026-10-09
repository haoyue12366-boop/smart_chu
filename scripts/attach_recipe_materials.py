"""将逐菜静态物料映射装配为领域物料和真实食物状态的有向图。

只装配调用者已创建的原子操作，不生成工艺、估时或人工批准。
"""

from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass
from fractions import Fraction
from heapq import heappop, heappush

from app.domain.material import MaterialRequirement, MaterialSpec


@dataclass
class _Food:
    key: str
    spec: dict
    source_requirement: dict | None = None
    producer: str | None = None
    used: Fraction = Fraction(0)

    @property
    def lineage(self) -> list[str]:
        return self.spec["composition"] or [self.spec["spec_id"]]


def _input(item: str | dict) -> tuple[str, Fraction]:
    key = item if isinstance(item, str) else item["id"]
    fraction = Fraction(1 if isinstance(item, str) else item.get("fraction", "1"))
    if not 0 < fraction <= 1:
        raise ValueError(f"物料份额必须大于零且不超过一批：{key}={fraction}")
    return key, fraction


def _scaled(quantity: dict | None, fraction: Fraction) -> dict | None:
    if quantity is None:
        return None
    number = Fraction(quantity["value"], quantity["scale"]) * fraction
    return {"value": number.numerator, "unit": quantity["unit"], "scale": number.denominator}


def _requirement(food: _Food, fraction: Fraction, requirement_id: str, evidence: str) -> dict:
    source = food.source_requirement
    data = {
        "requirement_id": requirement_id,
        "spec_id": food.spec["spec_id"],
        "quantity_kind": "QUALITATIVE",
        "qualitative_quantity": f"原配方本物料整批的{fraction}；实际产量未测定",
        "provenance_refs": [evidence],
    }
    if source is not None and source["quantity_kind"] in ("EXACT", "RANGE"):
        data.update(
            quantity_kind=source["quantity_kind"],
            quantity=_scaled(source.get("quantity"), fraction),
            upper_quantity=_scaled(source.get("upper_quantity"), fraction),
            qualitative_quantity=None,
        )
    elif source is not None:
        original = source.get("qualitative_quantity") or "原配方用量未定量"
        data["qualitative_quantity"] = f"{original}；本次使用原配方份额{fraction}"
    return MaterialRequirement.model_validate(data).model_dump(mode="json")


class _Attachment:
    def __init__(self, result: dict, mapping: dict) -> None:
        self.result = result
        self.mapping = mapping
        self.model = result["canonical"]
        self.evidence = f"ai-material:{result['recipe_id']}"
        self.operations = {op["operation_id"]: op for op in self.model["operations"]}
        self.foods: dict[str, _Food] = {}
        self.specs: dict[str, dict] = {}
        self.edges = {
            (dep["predecessor_id"], dep["successor_id"]) for dep in self.model["dependencies"]
        }
        self.counter = 0
        self.groups = {int(key): ids for key, ids in result["source_groups"].items()}
        for op in self.operations.values():
            op["material_inputs"] = []
            op["material_outputs"] = []
        existing = {spec["spec_id"]: spec for spec in self.model["material_specs"]}
        for source in self.model["ingredient_requirements"]:
            spec_id = source["spec_id"]
            if not spec_id.startswith("ingredient_"):
                raise ValueError(f"原料规格不是ingredient编号：{spec_id}")
            spec = MaterialSpec.model_validate(existing[spec_id]).model_dump(mode="json")
            key = spec_id.replace("ingredient_", "raw_", 1)
            if key in self.foods:
                raise ValueError(f"重复原料供应：{key}")
            self.specs[spec_id] = spec
            self.foods[key] = _Food(key, spec, source)

    def edge(self, predecessor: str, successor: str) -> None:
        if predecessor == successor or (predecessor, successor) in self.edges:
            return
        self.edges.add((predecessor, successor))
        self.model["dependencies"].append(
            {
                "predecessor_id": predecessor,
                "successor_id": successor,
                "min_lag_sec": 0,
                "max_lag_sec": None,
                "reason": "真实物料生产完成后方可消费",
                "evidence_refs": [self.evidence],
            }
        )

    def consume(self, key: str, fraction: Fraction, operation_id: str) -> None:
        if key not in self.foods:
            raise ValueError(f"物料不存在或尚未生产：{key}")
        food = self.foods[key]
        if food.used + fraction > 1:
            raise ValueError(f"物料累计消费超过一批：{key}")
        food.used += fraction
        operation = self.operations[operation_id]
        if food.producer == operation_id:
            # 同一原子操作内的相继静态阶段不向运行账暴露零时长内部库存。
            for requirement in operation["material_outputs"]:
                if requirement["spec_id"] == food.spec["spec_id"]:
                    operation["material_outputs"].remove(requirement)
                    if food.used < 1:
                        operation["material_outputs"].append(
                            _requirement(
                                food, 1 - food.used, requirement["requirement_id"], self.evidence
                            )
                        )
                    return
            raise ValueError(f"同工序内部物料没有对应产出：{key}")
        self.counter += 1
        operation["material_inputs"].append(
            _requirement(food, fraction, f"material_in_{self.counter:05}", self.evidence)
        )
        if food.producer is not None:
            self.edge(food.producer, operation_id)

    def produce(self, definition: dict, sources: list[str], operation_id: str) -> str:
        key = definition["id"]
        if key in self.foods or key in self.specs:
            raise ValueError(f"重复物料产出ID：{key}")
        lineage = sorted({part for source in sources for part in self.foods[source].lineage})
        spec = MaterialSpec.model_validate(
            {
                "spec_id": key,
                "ingredient_id": f"{self.result['recipe_id']}:{key}",
                "name": definition["name"],
                "state": definition["state"],
                "composition": lineage,
                "provenance_refs": [self.evidence],
                "review_status": "NEEDS_REVIEW",
            }
        ).model_dump(mode="json")
        food = _Food(key, spec, producer=operation_id)
        self.foods[key] = food
        self.specs[key] = spec
        self.counter += 1
        self.operations[operation_id]["material_outputs"].append(
            _requirement(food, Fraction(1), f"material_out_{self.counter:05}", self.evidence)
        )
        return key

    def stage(self, stage: dict, operations: list[str], ordinal: int) -> None:
        inputs = [_input(item) for item in stage["inputs"]]
        source_ids = [key for key, _ in inputs]
        if not source_ids or not stage["outputs"]:
            raise ValueError("食物物料阶段必须声明真实投入和产出")
        if any(key not in self.foods for key in source_ids):
            raise ValueError(f"阶段{stage['step']}引用未生产物料：{source_ids}")
        for definition in stage["outputs"]:
            if len(stage["outputs"]) > 1 and "from_inputs" not in definition:
                raise ValueError(f"多产出缺少明确来源from_inputs：{definition['id']}")
            sources = definition.get("from_inputs", source_ids)
            if not sources or not set(sources) <= set(source_ids):
                raise ValueError(f"产出谱系不属于本阶段投入：{definition['id']}")
        delayed: list[tuple[str, Fraction]] = []
        delayed_at = None
        if self.mapping.get("row") == 76 and stage["step"] == 5:
            delayed = [(key, share) for key, share in inputs if key in {"raw_007", "raw_008"}]
            inputs = [(key, share) for key, share in inputs if key not in {"raw_007", "raw_008"}]
            delayed_at = next(
                (op_id for op_id in operations if self.operations[op_id]["action"] == "ADD"),
                None,
            )
            if delayed and delayed_at is None:
                raise ValueError("披萨预烤后加馅缺少ADD原子操作，不能提前消费配料")
        carried = inputs
        for position, operation_id in enumerate(operations):
            if operation_id == delayed_at:
                carried += delayed
            for key, fraction in carried:
                self.consume(key, fraction, operation_id)
            if position == len(operations) - 1:
                for definition in stage["outputs"]:
                    self.produce(
                        definition, definition.get("from_inputs", source_ids), operation_id
                    )
            else:
                next_carried = []
                operation = self.operations[operation_id]
                for number, (key, _) in enumerate(carried):
                    food = self.foods[key]
                    intermediate = f"flow_{ordinal:03}_{position:02}_{number:02}"
                    self.produce(
                        {
                            "id": intermediate,
                            "name": food.spec["name"],
                            "state": f"{operation['description']}后的食物状态",
                        },
                        [key],
                        operation_id,
                    )
                    next_carried.append((intermediate, Fraction(1)))
                carried = next_carried

    def attach_stages(self) -> None:
        counts = Counter(int(stage["step"]) for stage in self.mapping["stages"])
        seen: Counter = Counter()
        for ordinal, stage in enumerate(self.mapping["stages"], 1):
            step = int(stage["step"])
            if step not in self.groups:
                raise ValueError(f"物料阶段缺少已构建操作组：{step}")
            physical = [
                op_id
                for op_id in self.groups[step]
                if self.operations[op_id]["action"] not in {"PREHEAT", "MILESTONE"}
                and not self.operations[op_id]["description"].startswith("设置设备程序/")
                and self.result.get("operation_metadata", {})
                .get(op_id, {})
                .get("material_handling")
                != "NONE"
                and self.result.get("operation_metadata", {}).get(op_id, {}).get("material_flow")
                is not False
            ]
            if not physical:
                raise ValueError(f"食物阶段{step}没有物理加工操作")
            if "operation_ids" in stage:
                selected = stage["operation_ids"]
                if (
                    not isinstance(selected, list)
                    or not selected
                    or any(not isinstance(op_id, str) for op_id in selected)
                    or len(set(selected)) != len(selected)
                    or any(op_id not in physical for op_id in selected)
                ):
                    raise ValueError(f"阶段{step}的operation_ids必须为本组不重复的物理操作")
                # 显式顺序成为真实依赖；既有直接或传递反向路径由最终拓扑检查拒绝。
                for before, after in zip(selected, selected[1:], strict=False):
                    self.edge(before, after)
            else:
                # 未声明精确操作时保持既有分配；单原子操作容许内部阶段连续转化。
                start = seen[step] * len(physical) // counts[step]
                end = (seen[step] + 1) * len(physical) // counts[step]
                selected = physical[start:end] or [physical[min(start, len(physical) - 1)]]
            seen[step] += 1
            self.stage(stage, selected, ordinal)

    def finish(self) -> None:
        finish_ops = [op for op in self.operations.values() if op["action"] == "FINISH"]
        if len(finish_ops) != 1:
            raise ValueError("必须存在唯一整菜FINISH操作")
        finish_id = finish_ops[0]["operation_id"]
        finals = self.mapping["final_outputs"]
        if not finals:
            raise ValueError("没有真实食物成品")
        for key in finals:
            self.consume(key, Fraction(1), finish_id)
        self.produce(
            {
                "id": "finaldish",
                "name": self.result["name"],
                "state": "全部必需食物分支完成并装盘上桌",
            },
            finals,
            finish_id,
        )

    def validate_and_export(self) -> None:
        successors: dict[str, list[str]] = defaultdict(list)
        incoming = dict.fromkeys(self.operations, 0)
        for before, after in self.edges:
            if before not in incoming or after not in incoming:
                raise ValueError("物料装配遇到悬空操作依赖")
            successors[before].append(after)
            incoming[after] += 1
        ranks = {op: rank for rank, op in enumerate(self.operations)}
        ready = [(ranks[op], op) for op in self.operations if incoming[op] == 0]
        ordered = []
        while ready:
            _, current = heappop(ready)
            ordered.append(self.operations[current])
            for after in successors[current]:
                incoming[after] -= 1
                if incoming[after] == 0:
                    heappush(ready, (ranks[after], after))
        if len(ordered) != len(self.operations):
            raise ValueError("物料生产者依赖与现有工艺依赖形成环")
        self.model["operations"] = ordered
        self.model["material_specs"] = list(self.specs.values())
        self.model["provenance_refs"] = list(
            dict.fromkeys([*self.model["provenance_refs"], self.evidence])
        )
        self.result["material_flow"] = {
            "origin": "MODEL_SUGGESTION",
            "review_status": "NEEDS_REVIEW",
            "source_row": self.mapping.get("row"),
            "remaining": {
                key: str(1 - food.used)
                for key, food in self.foods.items()
                if food.used < 1 and key != "finaldish"
            },
            "consumed_fractions": {key: str(food.used) for key, food in self.foods.items()},
            "producers": {key: food.producer for key, food in self.foods.items()},
            "notes": self.mapping.get("notes", []),
        }
        metadata = self.result.setdefault("operation_metadata", {})
        for operation in ordered:
            details = metadata.setdefault(operation["operation_id"], {})
            details["material_input_specs"] = [m["spec_id"] for m in operation["material_inputs"]]
            details["material_output_specs"] = [m["spec_id"] for m in operation["material_outputs"]]
            details["ingredient_refs"] = sorted(
                {
                    ancestor
                    for spec_id in details["material_input_specs"]
                    for ancestor in (self.specs[spec_id]["composition"] or [spec_id])
                }
            )
            for direction in ("input", "output"):
                details[f"{direction}_state"] = (
                    "；".join(
                        f"{self.specs[spec_id]['name']}：{self.specs[spec_id]['state']}"
                        for spec_id in details[f"material_{direction}_specs"]
                    )
                    or "无食物物料变动（设备或工艺控制）"
                )


def attach_materials(recipe_result: dict, map_entry: dict) -> None:
    """原位装配成功结果；校验失败不修改输入，由调用者再校验Canonical。"""
    if map_entry.get("recipe_id", recipe_result["recipe_id"]) != recipe_result["recipe_id"]:
        raise ValueError("物料映射与菜谱身份不一致")
    working = deepcopy(recipe_result)
    attachment = _Attachment(working, map_entry)
    attachment.attach_stages()
    attachment.finish()
    attachment.validate_and_export()
    recipe_result.clear()
    recipe_result.update(working)
