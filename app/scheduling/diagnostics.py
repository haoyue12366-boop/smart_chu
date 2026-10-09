"""限时纯可行性诊断；线性约束可控，全局约束保留并显式报告覆盖缺口。"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import TYPE_CHECKING
from uuid import uuid4

from app.domain.ports import Deadline
from app.domain.reports import DiagnosticReport, SolverBuildReport, SolverIndexMapping, SolveStatus
from app.domain.scheduling_problem import SchedulingProblem
from app.scheduling.model_builder import ModelBuilder
from app.scheduling.objectives import apply_stage

if TYPE_CHECKING:
    from ortools.sat.python import cp_model


class InfeasibilityAnalyzer:
    def diagnose(
        self,
        problem: SchedulingProblem,
        build_report: SolverBuildReport,
        failure_stage: str,
        deadline: Deadline,
    ) -> DiagnosticReport:
        started = time.monotonic_ns()
        if (
            problem.problem_hash != build_report.problem_hash
            or failure_stage != build_report.objective_stage
        ):
            raise ValueError("诊断问题、阶段与 SolverBuildReport 身份不匹配")
        cutoff = min(
            deadline.expires_at_ns,
            started + min(2000, problem.policy.diagnostic_total_budget_ms) * 1_000_000,
        )
        bounded = Deadline(expires_at_ns=cutoff)
        report = DiagnosticReport(
            diagnostic_id="diagnostic-" + str(uuid4()),
            problem_hash=problem.problem_hash,
            snapshot_id=problem.runtime.snapshot_id,
            original_status=build_report.solve_status or SolveStatus.UNKNOWN,
            failure_stage=failure_stage,
            scope="POLICY_MODEL",
            conflict_constraint_ids=(),
            evidence_refs=(),
            explanation_status="INCOMPLETE",
            base_model_feasibility="UNKNOWN",
            elapsed_ms=0,
            diagnosis_complete=False,
            solver_build_id=build_report.solver_build_id,
            candidate_truncated=problem.candidate_generation_report.candidate_truncated,
        )
        if report.original_status != SolveStatus.INFEASIBLE or time.monotonic_ns() >= cutoff:
            return report
        try:
            base = ModelBuilder(problem, bounded)
            base.build()
            # 锁定 OR-Tools 的此方法缺少返回类型标注。
            base.model.clear_objective()  # type: ignore[no-untyped-call]
            base_status, _ = self._solve(base.model, cutoff)
            feasibility = (
                "FEASIBLE"
                if base_status in ("FEASIBLE", "OPTIMAL")
                else "INFEASIBLE"
                if base_status == "INFEASIBLE"
                else "UNKNOWN"
            )
            report = report.model_copy(update={"base_model_feasibility": feasibility})
            has_stage = (
                build_report.stage_parameters is not None or failure_stage == "SERIAL_REFERENCE"
            )
            if has_stage:
                builder = ModelBuilder(problem, bounded)
                builder.build()
                if failure_stage == "SERIAL_REFERENCE":
                    builder.add_serial_order()
                elif build_report.stage_parameters is not None:
                    apply_stage(builder, build_report.stage_parameters)
                builder.model.clear_objective()  # type: ignore[no-untyped-call]
                report = report.model_copy(update={"scope": "OBJECTIVE_STAGE"})
            else:
                builder = base
            builder.check_budget()
            mappings, groups, uncontrolled = self._assumptions(builder)
            report = report.model_copy(
                update={
                    "diagnostic_build_id": "diagnostic-build-" + str(uuid4()),
                    "assumption_mappings": mappings,
                    "uncontrolled_constraint_count": uncontrolled,
                }
            )
            status, solver = self._solve(builder.model, cutoff)
            if status == "INFEASIBLE" and solver is not None:
                core = list(solver.sufficient_assumptions_for_infeasibility())
                checks = 0
                # 未受控全局约束始终保留；省略假设只允许对应组关闭，不改变线上模型。
                for literal in tuple(core):
                    if (
                        checks >= min(6, problem.policy.max_core_shrink_checks)
                        or time.monotonic_ns() >= cutoff
                    ):
                        break
                    trial = [v for v in core if v != literal]
                    builder.model.clear_assumptions()
                    builder.model.add_assumptions(
                        [builder.model.get_bool_var_from_proto_index(v) for v in trial]
                    )
                    trial_status, _ = self._solve(builder.model, cutoff)
                    checks += 1
                    if trial_status == "INFEASIBLE":
                        core = trial
                    elif trial_status not in ("OPTIMAL", "FEASIBLE"):
                        break
                ids = sorted({name for literal in core for name in groups[literal]})
                evidence = sorted(
                    {
                        ref
                        for record in problem.constraint_catalog
                        if record.constraint_id in ids
                        for ref in record.evidence_refs
                    }
                )
                report = report.model_copy(
                    update={
                        "conflict_constraint_ids": tuple(ids),
                        "evidence_refs": tuple(evidence),
                        "explanation_status": "PROVEN_CONFLICT",
                        "shrink_checks": checks,
                        "diagnosis_complete": True,
                        "suggested_review_actions": (
                            "冲突集合以未受控约束和变量域为背景；不代表全局最小或完整工艺根因。",
                            "检查编译时间域、时间网格和候选范围；诊断未放宽这些策略限制。",
                        )
                        + (
                            ("基础模型可行，检查本阶段附加上界。",)
                            if feasibility == "FEASIBLE"
                            else ()
                        ),
                    }
                )
            else:
                report = report.model_copy(
                    update={
                        "suggested_review_actions": (
                            "当前诊断没有复现不可行证明；保留原构建证据，不推断确定根因。",
                        )
                    }
                )
        except (TimeoutError, ValueError, TypeError) as exc:
            report = report.model_copy(update={"suggested_review_actions": (str(exc),)})
        return report.model_copy(
            update={"elapsed_ms": (time.monotonic_ns() - started) // 1_000_000}
        )

    @staticmethod
    def _solve(model: cp_model.CpModel, cutoff: int) -> tuple[str, cp_model.CpSolver | None]:
        from ortools.sat.python import cp_model

        remaining = (cutoff - time.monotonic_ns()) / 1e9
        if remaining <= 0.005:
            return "UNKNOWN", None
        if model.validate():
            return "MODEL_INVALID", None
        solver = cp_model.CpSolver()
        solver.parameters.num_search_workers = 1
        solver.parameters.max_time_in_seconds = max(0.001, remaining - 0.005)
        solver.parameters.random_seed = 42
        return solver.status_name(solver.solve(model)), solver

    @staticmethod
    def _assumptions(
        builder: ModelBuilder,
    ) -> tuple[tuple[SolverIndexMapping, ...], dict[int, tuple[str, ...]], int]:
        owners: dict[int, set[str]] = defaultdict(set)
        for identity, indices in builder.constraint_indices.items():
            for index in indices:
                owners[index].add(identity)
        for mapping in builder.additional_mappings:
            for index in mapping.proto_constraint_indices:
                owners[index].add(mapping.constraint_id)
        grouped: dict[tuple[str, ...], list[int]] = defaultdict(list)
        for index, constraint in enumerate(builder.model.proto.constraints):
            if constraint.has_linear() and owners[index]:
                grouped[tuple(sorted(owners[index]))].append(index)
        mappings: list[SolverIndexMapping] = []
        groups: dict[int, tuple[str, ...]] = {}
        for number, (identities, group_indices) in enumerate(grouped.items()):
            builder.check_budget()
            group_id = f"diagnostic-assumption-{number}"
            literal = builder.model.new_bool_var(group_id)
            for index in group_indices:
                builder.model.proto.constraints[index].enforcement_literal.append(literal.index)
            builder.model.add_assumption(literal)
            groups[literal.index] = identities
            mappings.extend(
                SolverIndexMapping(
                    constraint_id=identity,
                    proto_constraint_indices=tuple(group_indices),
                    variable_ids=(literal.name,),
                    assumption_group_id=group_id,
                )
                for identity in identities
            )
        uncontrolled = len(builder.model.proto.constraints) - sum(len(v) for v in grouped.values())
        return tuple(mappings), groups, uncontrolled
