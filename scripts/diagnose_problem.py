"""离线诊断保存的问题与指定构建报告；不会发布放宽约束后的计划。"""

import argparse
import json
import time
from pathlib import Path

from app.domain.ports import Deadline
from app.domain.reports import SolverBuildReport
from app.domain.scheduling_problem import SchedulingProblem
from app.scheduling.diagnostics import InfeasibilityAnalyzer
from scripts.run_sample_plan import write_json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--problem", required=True, type=Path)
    parser.add_argument("--build-report", required=True, type=Path)
    parser.add_argument("--build-id", help="输入为构建报告数组时必须指定")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    problem = SchedulingProblem.model_validate_json(args.problem.read_bytes())
    payload = json.loads(args.build_report.read_bytes())
    if isinstance(payload, list):
        found = [row for row in payload if row["solver_build_id"] == args.build_id]
        if len(found) != 1:
            parser.error("请提供文件中唯一的 --build-id")
        payload = found[0]
    build = SolverBuildReport.model_validate(payload)
    report = InfeasibilityAnalyzer().diagnose(
        problem,
        build,
        build.objective_stage,
        Deadline(expires_at_ns=time.monotonic_ns() + 2_000_000_000),
    )
    write_json(args.output, report.model_dump(mode="json"))
    print(report.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
