"""P2 CP-SAT 基础时间、覆盖、物料与工艺模型。"""

from __future__ import annotations

import time
from collections import defaultdict
from fractions import Fraction
from math import lcm
from typing import TYPE_CHECKING

from app.domain.carrier_timing import member_offsets
from app.domain.execution_timing import execution_intervals
from app.domain.ids import CarrierId, TaskId
from app.domain.objectives import ObjectiveStage
from app.domain.ports import Deadline
from app.domain.reports import SolverIndexMapping
from app.domain.scheduling_problem import SchedulingProblem
from app.domain.serial_order import serial_task_groups

if TYPE_CHECKING:
    from ortools.sat.python import cp_model

    from app.scheduling.resource_model import ResourceInterval


class ModelBuilder:
    def __init__(self, problem: SchedulingProblem, deadline: Deadline) -> None:
        from ortools.sat.python import cp_model

        self.problem = problem
        self.deadline = deadline
        self.model = cp_model.CpModel()
        self.starts: dict[TaskId, cp_model.IntVar] = {}
        self.ends: dict[TaskId, cp_model.IntVar] = {}
        self.selected: dict[CarrierId, cp_model.IntVar] = {}
        self.carrier_starts: dict[CarrierId, cp_model.IntVar] = {}
        self.carrier_ends: dict[CarrierId, cp_model.IntVar] = {}
        self.fixed: dict[TaskId, tuple[int, int]] = {}
        self.allocation_amounts: dict[tuple[CarrierId, str], cp_model.IntVar] = {}
        self.constraint_indices: dict[str, set[int]] = defaultdict(set)
        self.constraint_ids_by_category: dict[str, set[int]] = defaultdict(set)
        self.constraint_lookup: dict[tuple[str, str, str], set[int]] = defaultdict(set)
        for ordinal, record in enumerate(problem.constraint_catalog):
            self.constraint_ids_by_category[record.category].add(ordinal)
            for kind, identities in (
                ("task", tuple(t.root for t in record.task_ids)),
                ("carrier", tuple(c.root for c in record.carrier_ids)),
                ("resource", record.resource_ids),
            ):
                for identity in identities:
                    self.constraint_lookup[record.category, kind, identity].add(ordinal)
        self.human_intervals: list[ResourceInterval] = []
        self.layer_choices: list[tuple[ResourceInterval, cp_model.IntVar]] = []
        self.human_chain_enabled = False
        self.finish_spread_lower_bound_sec = 0
        self.sequence_arcs = 0
        self.objective_stage = "MAKESPAN"
        self.stage_parameters: ObjectiveStage | None = None
        self.additional_mappings: list[SolverIndexMapping] = []
        self.candidates = (
            *problem.standalone_candidates,
            *problem.shared_prep_candidates,
            *problem.thermal_batch_candidates,
            *problem.inventory_supply_candidates,
        )
        self.makespan = self.model.new_int_var(0, problem.horizon_sec, "makespan")
        self.objective_variable = self.makespan

    def check_budget(self) -> None:
        if time.monotonic_ns() >= self.deadline.expires_at_ns:
            raise TimeoutError("CP-SAT 建模截止时间已到")

    def mark(
        self,
        category: str,
        before: int,
        tasks: tuple[TaskId, ...] = (),
        carriers: tuple[CarrierId, ...] = (),
        resources: tuple[str, ...] = (),
    ) -> None:
        indices = range(before, len(self.model.proto.constraints))
        matches = set(self.constraint_ids_by_category.get(category, ()))
        # 每类条件内部取并集，条件之间取交集，保留原诊断映射语义。
        # 索引只供查询；每次匹配使用新集合，克隆阶段不会污染基础模型。
        for kind, identities in (
            ("task", tuple(t.root for t in tasks)),
            ("carrier", tuple(c.root for c in carriers)),
            ("resource", resources),
        ):
            if identities:
                matching_ids: set[int] = set()
                for identity in identities:
                    matching_ids.update(self.constraint_lookup.get((category, kind, identity), ()))
                matches.intersection_update(matching_ids)
        for ordinal in matches:
            self.constraint_indices[self.problem.constraint_catalog[ordinal].constraint_id].update(
                indices
            )

    def build(self) -> None:
        self.check_budget()
        problem, model = self.problem, self.model
        relevant = {task.task_id for task in problem.logical_tasks}
        for item in problem.fixed_task_fulfillments:
            for task_id in item.task_ids:
                if task_id in self.fixed:
                    raise ValueError("重复冻结库存满足需求")
                self.fixed[task_id] = item.satisfied_at_sec, item.satisfied_at_sec
        for execution in problem.fixed_executions:
            if execution.started_at is None:
                raise ValueError("冻结事实缺少开始时刻")
            start = problem.runtime.time_origin.offset(execution.started_at)
            if execution.finished_at is not None:
                end = problem.runtime.time_origin.offset(execution.finished_at)
            elif execution.remaining_sec is not None and execution.remaining_source_ref:
                end = problem.runtime.now_offset_sec + execution.remaining_sec
            else:
                raise ValueError("运行中工序没有剩余时长依据")
            for fixed_task_id in execution.task_ids:
                if fixed_task_id not in relevant:
                    continue
                if fixed_task_id in self.fixed:
                    raise ValueError("重复冻结任务")
                span = execution_intervals(execution, problem.runtime)[fixed_task_id]
                self.fixed[fixed_task_id] = span.start_sec, span.end_sec
        for task in problem.logical_tasks:
            self.check_budget()
            before = len(model.proto.constraints)
            tid = task.task_id
            start_var = model.new_int_var(0, problem.horizon_sec, tid.root + ":start")
            end_var = model.new_int_var(0, problem.horizon_sec, tid.root + ":end")
            self.starts[tid], self.ends[tid] = start_var, end_var
            model.add(end_var >= start_var)
            model.add(start_var >= task.earliest_start_sec)
            model.add(
                end_var
                <= (task.latest_end_sec if task.latest_end_sec is not None else problem.horizon_sec)
            )
            self.mark("TIME_DOMAIN", before, (tid,))
            before = len(model.proto.constraints)
            if tid in self.fixed:
                start, end = self.fixed[tid]
                model.add(start_var == start)
                model.add(end_var == end)
                self.mark("HISTORY", before, (tid,))
            else:
                model.add(start_var >= problem.runtime.now_offset_sec)
                model.add_modulo_equality(0, start_var, problem.policy.time_grid_sec)
                model.add_modulo_equality(0, end_var, problem.policy.time_grid_sec)
                self.mark("TIME_GRID", before)
        covers: dict[TaskId, list[cp_model.IntVar]] = defaultdict(list)
        for carrier in self.candidates:
            self.check_budget()
            if carrier.kind not in {
                "STANDALONE",
                "SHARED_PREP",
                "THERMAL_BATCH",
                "INVENTORY_SUPPLY",
            }:
                raise ValueError("当前载体类型不受支持")
            if len(set(carrier.covers)) != len(carrier.covers):
                raise ValueError("载体覆盖不能重复")
            carrier_task_id = carrier.covers[0]
            if any(t not in self.starts or t in self.fixed for t in carrier.covers):
                raise ValueError("候选重复覆盖历史或引用未知任务")
            selected = model.new_bool_var(carrier.carrier_id.root + ":selected")
            self.selected[carrier.carrier_id] = selected
            before = len(model.proto.constraints)
            if carrier.kind == "THERMAL_BATCH":
                outer_start = model.new_int_var(
                    0, problem.horizon_sec, carrier.carrier_id.root + ":start"
                )
                outer_end = model.new_int_var(
                    0, problem.horizon_sec, carrier.carrier_id.root + ":end"
                )
                model.add(outer_end == outer_start + carrier.duration_sec).only_enforce_if(selected)
            else:
                outer_start = self.starts[carrier_task_id]
                outer_end = self.ends[carrier_task_id]
                model.add(outer_end == outer_start + carrier.duration_sec).only_enforce_if(selected)
            self.carrier_starts[carrier.carrier_id] = outer_start
            self.carrier_ends[carrier.carrier_id] = outer_end
            for member_id, (offset_start, offset_end) in member_offsets(carrier).items():
                covers[member_id].append(selected)
                model.add(self.starts[member_id] == outer_start + offset_start).only_enforce_if(
                    selected
                )
                model.add(self.ends[member_id] == outer_start + offset_end).only_enforce_if(
                    selected
                )
            self.mark("DURATION", before, carrier.covers, (carrier.carrier_id,))
        for task in problem.logical_tasks:
            if task.task_id not in self.fixed:
                before = len(model.proto.constraints)
                model.add_exactly_one(covers[task.task_id])
                self.mark("COVERAGE", before, (task.task_id,))
        for dep in problem.dependencies:
            if any(
                {dep.predecessor_id, dep.successor_id} <= set(item.task_ids)
                for item in problem.fixed_task_fulfillments
            ):
                continue
            before = len(model.proto.constraints)
            lag = self.starts[dep.successor_id] - self.ends[dep.predecessor_id]
            guards = [
                self.selected[carrier.carrier_id].Not()
                for carrier in problem.inventory_supply_candidates
                if {dep.predecessor_id, dep.successor_id} <= set(carrier.covers)
            ]
            model.add(lag >= dep.min_lag_sec).only_enforce_if(guards)
            if dep.max_lag_sec is not None:
                model.add(lag <= dep.max_lag_sec).only_enforce_if(guards)
            self.mark("PRECEDENCE", before, (dep.predecessor_id, dep.successor_id))
        for relation in problem.mandatory_programs.time_relations:
            before = len(model.proto.constraints)
            left = (self.starts if relation.left_anchor == "START" else self.ends)[
                relation.left_task
            ]
            right = (self.starts if relation.right_anchor == "START" else self.ends)[
                relation.right_task
            ]
            model.add(right - left >= relation.min_offset_sec)
            if relation.max_offset_sec is not None:
                model.add(right - left <= relation.max_offset_sec)
            members = (relation.left_task, relation.right_task)
            self.mark("TIME_RELATION", before, members)
            self.mark("FIXED_PROGRAM", before, members)
        self._materials()
        from app.scheduling.inventory_constraints import add_inventory_constraints

        add_inventory_constraints(self)
        from app.scheduling.material_constraints import add_material_allocation_constraints

        material_before = len(model.proto.constraints)
        add_material_allocation_constraints(self)
        self.mark("MATERIAL", material_before)
        self.check_budget()
        from app.scheduling.resource_model import add_resource_constraints

        add_resource_constraints(self)
        from app.scheduling.finish_spread_bounds import minimum_finish_spread

        self.finish_spread_lower_bound_sec = minimum_finish_spread(self)
        self.check_budget()
        if not self.ends:
            raise ValueError("空菜单不能生成计划")
        model.add_max_equality(self.makespan, list(self.ends.values()))
        model.minimize(self.makespan)

    def _materials(self) -> None:
        from app.domain.recovery import retained_input_tasks

        supplies = {s.supply_id: s for s in self.problem.material_flow.supplies}
        totals: dict[str, list[tuple[Fraction, cp_model.LinearExpr | int]]] = defaultdict(list)
        supplied = {task for item in self.problem.fixed_task_fulfillments for task in item.task_ids}
        supplied.update(retained_input_tasks(self.problem.runtime))
        for demand in self.problem.material_flow.demands:
            self.check_budget()
            supply = supplies.get(demand.supply_id)
            if supply is None or demand.task_id not in self.starts:
                raise ValueError("悬空物料供需")
            if demand.task_id in supplied:
                continue
            before = len(self.model.proto.constraints)
            if demand.task_id not in self.fixed:
                totals[supply.supply_id].append(
                    (
                        Fraction(demand.share_numerator, demand.share_denominator),
                        self.performed(demand.task_id),
                    )
                )
                self.model.add(
                    self.starts[demand.task_id] >= supply.available_at_sec
                ).only_enforce_if(
                    [selected.Not() for selected in self.inventory_selectors(demand.task_id)]
                )
            if supply.producer_task_id is not None:
                self.model.add(
                    self.starts[demand.task_id] >= self.ends[supply.producer_task_id]
                ).only_enforce_if(
                    [selected.Not() for selected in self.inventory_selectors(demand.task_id)]
                )
            self.mark("MATERIAL", before, (demand.task_id,))
        for ident, terms in totals.items():
            supply = supplies[ident]
            scale = lcm(
                supply.available_share_denominator, *(amount.denominator for amount, _ in terms)
            )
            if scale > 2**40:
                raise ValueError("库存数量缩放超过安全整数范围")
            before = len(self.model.proto.constraints)
            self.model.add(
                sum(int(amount * scale) * active for amount, active in terms)
                <= supply.available_share_numerator * (scale // supply.available_share_denominator)
            )
            self.mark("MATERIAL", before)

    def inventory_selectors(self, task: TaskId) -> tuple[cp_model.IntVar, ...]:
        return tuple(
            self.selected[carrier.carrier_id]
            for carrier in self.problem.inventory_supply_candidates
            if task in carrier.covers
        )

    def performed(self, task: TaskId) -> cp_model.LinearExpr | int:
        return 1 - sum(self.inventory_selectors(task))

    def add_serial_order(self) -> None:
        self.objective_stage = "SERIAL_REFERENCE"
        previous = None
        for ordinal, tasks in enumerate(serial_task_groups(self.problem)):
            self.check_budget()
            completion = self.model.new_int_var(0, self.problem.horizon_sec, f"serial:{ordinal}")
            before = len(self.model.proto.constraints)
            self.model.add_max_equality(completion, [self.ends[t] for t in tasks])
            if previous is not None:
                for task in tasks:
                    self.model.add(self.starts[task] >= previous)
            self.additional_mappings.append(
                SolverIndexMapping(
                    constraint_id=f"serial-reference-order:{ordinal}",
                    variable_ids=tuple(self.starts[t].name for t in tasks),
                    proto_constraint_indices=tuple(
                        range(before, len(self.model.proto.constraints))
                    ),
                )
            )
            previous = completion
