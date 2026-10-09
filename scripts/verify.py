"""显式任务依赖、真实命令与不可跳过的阶段验收。"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_command(command: list[str], cwd: Path, timeout_sec: int = 300) -> dict:
    started = time.monotonic()
    actual = list(command)
    is_pytest = "pytest" in command
    with tempfile.TemporaryDirectory(prefix="verify-", dir=cwd) as temp:
        junit = Path(temp) / "results.xml"
        if is_pytest:
            actual += [
                f"--junitxml={junit}",
                "-p",
                "no:cacheprovider",
                "--basetemp",
                str(Path(temp) / "pytest"),
            ]
        try:
            process = subprocess.run(
                actual,
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_sec,
                env={**os.environ, "PYTHONUTF8": "1"},
            )
            exit_code, output = process.returncode, process.stdout + process.stderr
        except (OSError, subprocess.TimeoutExpired) as exc:
            exit_code, output = -1, str(exc)
        count = passed = failed = skipped = 0
        if is_pytest and junit.exists():
            try:
                evidence = ET.parse(junit).getroot()
                cases = evidence.findall(".//testcase")
                count = len(cases)
                skipped = sum(case.find("skipped") is not None for case in cases)
                failed = sum(
                    case.find("failure") is not None or case.find("error") is not None
                    for case in cases
                )
                passed = count - skipped - failed
                properties = [
                    {"name": item.get("name"), "value": item.get("value")}
                    for item in evidence.findall(".//properties/property")
                ]
                if properties:
                    output += "\n实际测试统计：" + json.dumps(properties, ensure_ascii=False)
            except ET.ParseError:
                output += "\nJUnit XML 无法解析"
        success = exit_code == 0 and (not is_pytest or (count > 0 and skipped == failed == 0))
    return {
        "command": command,
        "exit_code": exit_code,
        "elapsed_ms": round((time.monotonic() - started) * 1000),
        "collected_count": count,
        "passed_count": passed,
        "failed_count": failed,
        "skipped_count": skipped,
        "passed": success,
        "output": output,
    }


def fingerprint(root: Path, artifacts: tuple[str, ...] = ()) -> tuple[str, dict[str, str]]:
    """绑定源文件和必需产物；安装副本、运行状态与未选用旧发布不递归作源码。"""
    paths = [root / name for name in ("pyproject.toml", "uv.lock", ".python-version")]
    for directory in ("app", "scripts", "tests"):
        paths.extend((root / directory).rglob("*"))
    generated_data = {"delivery", "verification", "runtime", "releases", "preparations"}
    for directory, names, files in (root / "data").walk():
        if directory == root / "data":
            names[:] = [name for name in names if name not in generated_data]
        paths.extend(directory / name for name in files)
    # 冻结发布及独立报告由所选任务显式声明并绑定内容，不以扫描所有历史目录替代。
    paths.extend(root / name for name in artifacts)
    paths.extend((root / "docs").glob("*"))
    paths.extend(
        p
        for p in (root / "benchmarks").rglob("*.py")
        if p.relative_to(root / "benchmarks").parts[0] != "reports"
    )
    paths.extend((root / "benchmarks/scenarios").rglob("*"))
    paths.extend((root / "web").glob("*.*"))
    for directory in ("web/src", "web/tests"):
        paths.extend((root / directory).rglob("*"))
    hashes = {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(set(paths))
        if p.is_file()
        and "__pycache__" not in p.parts
        and p.suffix != ".pyc"
        and (p.suffix != ".zip" or p.relative_to(root).as_posix() in artifacts)
    }
    digest = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    return digest, hashes


def verify(
    phase: str, gate: str | None, manifest_path: Path, root: Path, task: str | None = None
) -> dict:
    started = time.monotonic()
    report = {
        "schema_version": "1.0",
        "phase": phase,
        "gate": gate,
        "task": task,
        "status": "FAILED",
        "started_at": datetime.now(UTC).isoformat(),
        "commands": [],
        "errors": [],
        "tasks": {},
    }
    fingerprint_artifacts: set[str] = set()
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        key = f"{phase}:{gate}" if gate else phase
        entry = manifest["phases"].get(key)
        if not entry or not entry.get("tasks"):
            raise ValueError(f"阶段未登记或任务集为空：{key}")
        success_status = "VERIFIED"
        overrides = {}
        if gate == "development":
            authorization = entry["development_authorization_artifact"]
            authorization_path = (root / authorization["path"]).resolve()
            if not authorization_path.is_relative_to(root.resolve()):
                raise ValueError("开发授权来源越出项目目录")
            authorization_bytes = authorization_path.read_bytes()
            if hashlib.sha256(authorization_bytes).hexdigest() != authorization["sha256"]:
                raise ValueError("开发授权来源哈希不一致")
            auth = json.loads(authorization_bytes)
            if auth.get("source") != "USER_MESSAGE" or not entry.get(
                "deferred_formal_requirements"
            ):
                raise ValueError("开发验收必须绑定显式授权并保留正式门槛待办")
            overrides = entry.get("development_dependency_overrides", {})
            success_status = "DEVELOPMENT_VERIFIED"
            report["development_authorization_ref"] = auth.get(
                "authorization_id", authorization["path"]
            )
            report["deferred_formal_requirements"] = entry["deferred_formal_requirements"]
            report["development_dependency_overrides"] = overrides
        elif entry.get("development_dependency_overrides"):
            raise ValueError("正式验收不能使用开发依赖替代")
        registered = manifest.get("tasks", {})

        def task_dependencies(task_id: str) -> list[str]:
            return overrides.get(task_id, registered[task_id].get("dependencies", []))

        order: list[str] = []
        visiting: set[str] = set()

        def visit(task_id: str) -> None:
            if task_id in order:
                return
            if task_id in visiting:
                raise ValueError(f"任务依赖环：{task_id}")
            if task_id not in registered:
                raise ValueError(f"任务尚未实现或未登记：{task_id}")
            visiting.add(task_id)
            for dep in task_dependencies(task_id):
                visit(dep)
            visiting.remove(task_id)
            order.append(task_id)

        phase_visiting: set[str] = set()
        phase_dependencies: dict[str, list[str]] = {}

        def visit_phase(phase_key: str, selected_task: str | None = None) -> list[str]:
            if phase_key in phase_visiting:
                raise ValueError(f"阶段依赖环：{phase_key}")
            specification = manifest["phases"].get(phase_key)
            if not specification or not specification.get("tasks"):
                raise ValueError(f"阶段未登记或任务集为空：{phase_key}")
            phase_visiting.add(phase_key)
            inherited: list[str] = []
            for dependency in specification.get("required_phase_gates", []):
                inherited.extend(visit_phase(dependency))
            chosen = [selected_task] if selected_task else specification["tasks"]
            if selected_task and selected_task not in specification["tasks"]:
                raise ValueError("所选任务不属于该阶段")
            for current in chosen:
                phase_dependencies[current] = list(dict.fromkeys(inherited))
                visit(current)
            phase_visiting.remove(phase_key)
            return list(dict.fromkeys([*inherited, *chosen]))

        visit_phase(key, task)
        for task_id in order:
            specification = registered[task_id]
            files = specification.get("artifacts", []) + specification.get("tests", [])
            if not specification.get("tests"):
                raise ValueError(f"{task_id} 必需测试为空")
            for name in files:
                path = (root / name).resolve()
                if not path.is_relative_to(root.resolve()) or not path.is_file():
                    raise ValueError(f"{task_id} 缺少必需产物/测试：{name}")
                fingerprint_artifacts.add(name)
        for args in manifest.get("quality_commands", []):
            command = [sys.executable if x == "{python}" else x for x in args]
            result = run_command(command, root)
            report["commands"].append(result)
        quality_passed = all(r["passed"] for r in report["commands"])
        for task_id in order:
            print(f"开始验收 {task_id}", file=sys.stderr, flush=True)
            result = run_command(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    *registered[task_id]["tests"],
                    "-q",
                    "-o",
                    "addopts=",
                ],
                root,
                timeout_sec=registered[task_id].get("timeout_sec", 300),
            )
            report["commands"].append(result)
            own = result["passed"]
            dependencies = all(
                report["tasks"][dep]["status"] == success_status
                for dep in [
                    *task_dependencies(task_id),
                    *phase_dependencies.get(task_id, []),
                ]
            )
            report["tasks"][task_id] = {
                "status": success_status if own and dependencies and quality_passed else "FAILED",
                "own_checks_passed": own,
                "quality_checks_passed": quality_passed,
                "dependencies_passed": dependencies,
            }
            print(
                f"{task_id}: {report['tasks'][task_id]['status']}；"
                f"通过 {result['passed_count']}、失败 {result['failed_count']}、"
                f"跳过 {result['skipped_count']}；{result['elapsed_ms']} ms",
                file=sys.stderr,
                flush=True,
            )
        if all(r["passed"] for r in report["commands"]):
            report["status"] = success_status
    except (OSError, ValueError, KeyError) as exc:
        report["errors"].append(str(exc))
    report["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    report["finished_at"] = datetime.now(UTC).isoformat()
    report["code_fingerprint"], report["artifact_hashes"] = fingerprint(
        root, tuple(sorted(fingerprint_artifacts))
    )
    report["environment"] = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
        "mode": "offline",
        "random_seed": None,
        "dependencies": {
            name: importlib.metadata.version(name)
            for name in ("pydantic", "ortools", "sqlalchemy", "pytest", "ruff", "mypy")
            if importlib.util.find_spec(name) is not None
        },
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", required=True, choices=[f"P{i}" for i in range(7)])
    parser.add_argument("--gate", choices=("core", "full", "development"))
    parser.add_argument("--task")
    args = parser.parse_args()
    report = verify(
        args.phase, args.gate, ROOT / "scripts/verification_manifest.json", ROOT, args.task
    )
    target = ROOT / "benchmarks/reports/verification"
    target.mkdir(parents=True, exist_ok=True)
    raw_name = args.task or (args.phase + ("-" + args.gate if args.gate else ""))
    name = "".join(char for char in raw_name if char.isascii() and (char.isalnum() or char == "-"))
    output = target / f"{name}.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": report["status"],
                "report": str(output),
                "errors": report["errors"],
                "tasks": report["tasks"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["status"] in {"VERIFIED", "DEVELOPMENT_VERIFIED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
