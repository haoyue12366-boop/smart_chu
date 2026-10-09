"""记录实际 protobuf 二进制大小及本次构建索引，而非 Compiler 预估值。"""

from __future__ import annotations

from collections import Counter
from functools import lru_cache
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING
from uuid import uuid4

from app.domain.policy import ModelSize
from app.domain.reports import SolverBuildReport, SolverIndexMapping

if TYPE_CHECKING:
    from app.scheduling.model_builder import ModelBuilder


@lru_cache(maxsize=1)
def _solver_version() -> str:
    return version("ortools")


def warmup_build_reporting() -> None:
    """就绪前加载报告转换依赖，避免首次短阶段包含磁盘导入开销。"""
    from ortools.sat import cp_model_pb2  # type: ignore[attr-defined]

    cp_model_pb2.CpModelProto().ParseFromString(b"")
    _solver_version()


def make_build_report(builder: ModelBuilder, elapsed_ms: int) -> SolverBuildReport:
    # 锁定依赖未附 protobuf 导入的完整类型；忽略仅限此转换边界。
    from ortools.sat import cp_model_pb2  # type: ignore[attr-defined]

    # 使用公开二进制导出，避免人工序列目标的大量约束经文本解析耗尽短阶段预算。
    builder.check_budget()
    proto = cp_model_pb2.CpModelProto()
    # Windows 原生导出不接受含中文的绝对路径；临时目录相对当前工作目录
    # 使用纯 ASCII 名称，Python 读取保留完整路径，不改变进程工作目录。
    with TemporaryDirectory(prefix="smart-cooking-proto-", dir=".") as folder:
        path = Path(folder).resolve() / "model.bin"
        export_path = path.relative_to(Path.cwd().resolve())
        if not builder.model.export_to_file(str(export_path)):
            raise ValueError("无法导出本次实际求解模型")
        proto.ParseFromString(path.read_bytes())
    builder.check_budget()
    counts = Counter(c.WhichOneof("constraint") or "unset" for c in proto.constraints)
    optional_intervals = sum(
        c.HasField("interval") and bool(c.enforcement_literal) for c in proto.constraints
    )
    boolean_variables = sum(tuple(v.domain) == (0, 1) for v in proto.variables)
    mappings = []
    for record in builder.problem.constraint_catalog:
        builder.check_budget()
        variable_ids = tuple(
            v.name
            for task in record.task_ids
            if task in builder.starts
            for v in (builder.starts[task], builder.ends[task])
        )
        mappings.append(
            SolverIndexMapping(
                constraint_id=record.constraint_id,
                variable_ids=variable_ids,
                proto_constraint_indices=tuple(
                    sorted(builder.constraint_indices[record.constraint_id])
                ),
            )
        )
    return SolverBuildReport(
        problem_hash=builder.problem.problem_hash,
        solver_build_id="cp-sat-" + str(uuid4()),
        objective_stage=builder.objective_stage,
        solver_version=_solver_version(),
        actual_model_size=ModelSize(
            total_variables=len(proto.variables),
            boolean_variables=boolean_variables,
            optional_intervals=optional_intervals,
            total_constraints=len(proto.constraints),
            sequence_arcs=builder.sequence_arcs,
        ),
        constraint_mappings=(*mappings, *builder.additional_mappings),
        serialized_proto_bytes=proto.ByteSize(),
        build_time_ms=elapsed_ms,
        logical_tasks=len(builder.problem.logical_tasks),
        fixed_executions=len(builder.problem.fixed_executions),
        selected_path_alternatives=len(builder.candidates),
        candidate_truncated=builder.problem.candidate_generation_report.candidate_truncated,
        may_lose_optimum=builder.problem.candidate_generation_report.may_lose_optimum,
        candidate_enumeration_complete=builder.problem.candidate_generation_report.enumeration_complete,
        generated_candidate_count=builder.problem.candidate_generation_report.generated_count,
        pruning_counts=dict(Counter(r.kind for r in builder.problem.pruning_records)),
        constraints_by_type=dict(counts),
        stage_parameters=builder.stage_parameters,
    )
