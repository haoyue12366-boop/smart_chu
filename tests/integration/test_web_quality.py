"""阶段入口真实执行前端质量命令，不能以构建产物存在替代验收。"""

import subprocess

from tests.web_verification_support import checked_cases, npm_command, record_web, run_web


def test_actual_pinned_web_toolchain_typecheck_unit_format_and_build(
    tmp_path, record_testsuite_property
):
    commands = npm_command()
    node_version = subprocess.check_output([commands[0], "--version"], text=True).strip()
    npm_version = subprocess.check_output(commands[:2] + ["--version"], text=True).strip()
    assert node_version == "v25.8.1" and npm_version == "11.11.0"
    junit = tmp_path / "frontend-unit.xml"
    evidence = [
        run_web(["run", "typecheck"]),
        run_web(["run", "format:check"]),
        run_web(["run", "test:unit", "--", "--reporter=junit", f"--outputFile={junit}"]),
        run_web(["run", "build"]),
    ]
    evidence[2]["test_results"] = checked_cases(junit)
    record_web(record_testsuite_property, "p5_frontend_quality_commands", evidence)
