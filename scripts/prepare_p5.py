"""归档 P5 实施输入和协议设计；不改写发布、运行事实或阶段验收入口。"""

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import tomllib
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any
from zipfile import ZipFile

from app.domain.ids import RecipeId
from app.domain.knowledge import ReleaseRef
from app.knowledge.repository import SnapshotKnowledgeRepository
from scripts.p5_preparation_design import (
    TASK_IDS,
    api_scenarios,
    competition_profiles,
    gate_design,
    protocol_questions,
)

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = Path("data/preparations/p5-v1")
P4_INPUT = "data/preparations/p4-v1/p4_inputs.json"
POLICY = "data/policies/p4-runtime-v1.json"
P4_GATE = "benchmarks/reports/verification/P4-development.json"
P4_COMMAND = "data/verification/P4-development-command-run2.json"
P4_PLANS = "data/verification/P4-final-saved-plans-run2.json"
P4_BENCHMARK = "benchmarks/reports/P4-runtime-v5/report.json"
CSV_PATHS = (
    "docs/recipes_100.csv",
    "docs/recipes_100_详细步骤.csv",
    "docs/recipes_100_详细步骤_完善版.csv",
)
OFFICIAL_PATHS = (
    "docs/答疑9.30.txt",
    "docs/智能烹饪调度Agent接口规范.md",
    "docs/方太专项赛.docx",
    "docs/设备参数清单参考.json",
    *CSV_PATHS,
)
TECHNICAL_PATHS = (
    "AGENTS.md",
    "docs/AGENTS.md",
    "docs/2026-09-22-智能烹饪调度技术方案与架构设计.md",
    "docs/P4准备与规则基线.md",
    "docs/P5准备与接口基线.md",
    "pyproject.toml",
    "uv.lock",
    "scripts/verification_manifest.json",
    "scripts/p5_preparation_design.py",
    "scripts/prepare_p5.py",
    "scripts/check_p5_preparation.py",
)
EVIDENCE_PATHS = (
    P4_INPUT,
    POLICY,
    "data/development/authorizations/p4-delegation-v1.json",
    P4_GATE,
    P4_COMMAND,
    P4_PLANS,
    P4_BENCHMARK,
    "data/verification/P4-final-quality-run3.json",
    "data/verification/P4-final-initial-timeout-review.json",
)


def read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise ValueError(f"需要 JSON 对象：{path}")
    return value


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def contained(root: Path, value: str | Path) -> Path:
    path = (root / value).resolve()
    require(path.is_relative_to(root.resolve()), f"路径超出指定目录：{value}")
    return path


def write_new(path: Path, value: object) -> None:
    payload = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(payload)


def command_version(name: str) -> dict[str, str]:
    executable = shutil.which(name)
    require(executable is not None, f"本机缺少 {name}")
    result = subprocess.run(
        [str(executable), "--version"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
    )
    return {"executable": str(executable), "version": result.stdout.strip()}


def environment() -> dict[str, Any]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = {}
    for requirement in project["project"]["dependencies"]:
        package, expected = requirement.split("==")
        observed = version(package)
        require(observed == expected, f"后端依赖与锁定基线不符：{package}")
        dependencies[package] = observed
    node = command_version("node")
    npm = command_version("npm.cmd" if os.name == "nt" else "npm")
    require(node["version"] == "v25.8.1", "用户指定的 Node 25.8.1 未生效")
    require(npm["version"] == "11.11.0", "本机 npm 11.11.0 基线未生效")
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "dependencies": dependencies,
        "node": node,
        "npm": npm,
        "node_selection_authority": "USER_INSTRUCTION_2026-10-02",
        "node_selection_instruction": "可以改成使用本机node25吗",
        "changes_global_installation_or_path": False,
        "frontend_build_status": "NOT_EXECUTED_P5-06",
        "browser_test_status": "NOT_EXECUTED_P5-08",
        "p5_paths_present": {
            name: (ROOT / name).exists() for name in ("app/main.py", "app/api", "web")
        },
        "model": {
            "api_key_present": bool(os.environ.get("DEEPSEEK_API_KEY")),
            "key_content_archived": False,
            "live_call_status": "NOT_EXECUTED",
        },
    }


def recipe_catalog(inputs: dict[str, Any]) -> list[dict[str, Any]]:
    repository = SnapshotKnowledgeRepository(contained(ROOT, inputs["release_root"]))
    ref = ReleaseRef.model_validate(inputs["release"])
    repository.load(ref)
    with repository.acquire(ref) as lease:
        ids = tuple(r.recipe_id for r in lease.loaded.snapshot.knowledge.recipes)
        knowledge = lease.select(tuple(RecipeId(rid.root) for rid in ids))
    require(len(knowledge.recipes) == 100, "固定发布不含完整 100 菜")
    return [
        {
            "id": recipe.recipe_id.root,
            "name": recipe.name,
            "operation_count": len(recipe.operations),
            "recipe_semantic_hash": recipe.semantic_hash(),
        }
        for recipe in knowledge.recipes
    ]


def scheduling_excerpt() -> str:
    # 仅读取比赛说明 DOCX 的 OOXML；不读取用户排除的项目 ZIP。
    with ZipFile(ROOT / "docs/方太专项赛.docx") as document:
        tree = ET.fromstring(document.read("word/document.xml"))
    namespaces = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs = [
        "".join(t.text or "" for t in p.findall(".//w:t", namespaces))
        for p in tree.findall(".//w:p", namespaces)
    ]
    selected = paragraphs[33:61]
    require(any("ZX-2026-0302" in p for p in selected), "比赛说明段落位置改变")
    return (
        "来源：docs/方太专项赛.docx；只摘录烹饪调度赛题第34至61段。\n"
        + "\n".join(f"[{index}] {text}" for index, text in enumerate(selected, start=34))
        + "\n"
    )


def archive_sources(output: Path) -> list[dict[str, Any]]:
    rows = []
    groups = (
        ("OFFICIAL_MATERIAL", OFFICIAL_PATHS),
        ("TECHNICAL_INPUT", TECHNICAL_PATHS),
        ("P4_EVIDENCE", EVIDENCE_PATHS),
        ("PROGRESS_LEDGER_SNAPSHOT", ("docs/task.md",)),
    )
    for role, paths in groups:
        for name in paths:
            source = contained(ROOT, name)
            archive_name = "source_inputs/" + name
            destination = contained(output, archive_name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as stream:
                stream.write(source.read_bytes())
            rows.append(
                {
                    "path": name,
                    "archive_path": archive_name,
                    "sha256": sha256(source),
                    "role": role,
                    "require_current_hash": role != "PROGRESS_LEDGER_SNAPSHOT",
                }
            )
    return rows


def prepare(output: Path) -> None:
    output = contained(ROOT, output)
    require(not output.exists(), "准备目录已存在；请检查既有版本或指定新的版本，禁止覆盖")
    inputs = read(ROOT / P4_INPUT)
    catalog = recipe_catalog(inputs)
    observed_environment = environment()
    profiles = competition_profiles()
    scenarios = api_scenarios(catalog)
    verification = read(ROOT / "scripts/verification_manifest.json")
    competition_text = scheduling_excerpt()
    sources = archive_sources(output)
    source_by_path = {row["path"]: row for row in sources}
    write_new(output / "source_manifest.json", {"schema_version": "1", "sources": sources})
    write_new(output / "recipe_catalog.json", {"release": inputs["release"], "recipes": catalog})
    write_new(output / "competition_profiles.json", profiles)
    write_new(output / "api_scenarios.json", scenarios)
    write_new(output / "protocol_questions.json", protocol_questions())
    write_new(output / "environment.json", observed_environment)
    write_new(output / "gate_design.json", gate_design(verification["phases"]["P5"]))
    write_new(
        output / "p5_inputs.json",
        {
            "schema_version": "p5-preparation-1",
            "purpose": "P5_IMPLEMENTATION_PREPARATION_ONLY",
            "recorded_at": datetime.now(UTC).isoformat(),
            "release_root": inputs["release_root"],
            "release": inputs["release"],
            "runtime_policy": POLICY,
            "authorization": inputs["authorization"],
            "prerequisites": {
                "p4_gate": P4_GATE,
                "actual_command": P4_COMMAND,
                "saved_plan_validation": P4_PLANS,
                "internal_benchmark": P4_BENCHMARK,
            },
            "input_bindings": {
                path: source_by_path[path]["sha256"]
                for path in (P4_INPUT, POLICY, inputs["authorization"], P4_GATE, P4_COMMAND)
            },
            "counts": {
                "recipes": len(catalog),
                "operations": sum(row["operation_count"] for row in catalog),
                "device_profiles": 27,
                "designed_scenarios": len(scenarios["cases"]),
            },
            "duplicate_names": {
                name: count
                for name, count in Counter(row["name"] for row in catalog).items()
                if count > 1
            },
            "implementation_tasks": dict.fromkeys(TASK_IDS, "NOT_STARTED"),
            "execution_order": [
                "P5-01",
                "P5-03",
                "P5-02",
                "P5-05",
                "P5-04",
                "P5-06",
                "P5-07",
                "P5-08",
            ],
            "frontend_selection": {"node": "25.8.1", "npm": "11.11.0"},
            "technical_changes_require_new_preparation_version": True,
            "progress_ledger_can_append_after_preparation": True,
            "official_dynamic_integration_status": "NOT_EXECUTED",
            "p5_phase_gate_status": "NOT_EXECUTED",
        },
    )
    excerpt = output / "source_extracts/competition_scheduling.txt"
    excerpt.parent.mkdir(parents=True, exist_ok=True)
    with excerpt.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(competition_text)
    relative_output = output.relative_to(ROOT).as_posix()
    readme = (
        "# P5 实施准备包\n\n"
        "本包仅固定实施输入、开发协议与场景，不代表 P5 功能或官方动态接口已通过。\n\n"
        "先读取 docs/P5准备与接口基线.md、docs/task.md 和 p5_inputs.json。\n"
        "用户选择本机 Node 25.8.1 / npm 11.11.0；前端真实安装、构建与浏览器验证在 P5 执行。\n"
        "开发 task_id 查询参数、累计响应、SESSION_ORIGIN 均明确标为项目草案。\n"
        "PLAN_ONLY 无执行反馈不推进；人工和模拟反馈复用 P4 事实冻结。\n\n"
        f"检查：uv run --locked python -m scripts.check_p5_preparation --root {relative_output} "
        "--output <新的报告路径>\n\n"
        "准备目录和检查报告拒绝覆盖。技术输入变更生成新的版本。\n"
        "source_inputs/ 中的 task.md 是生成时快照，当前唯一进度仍为 docs/task.md；"
        "后续追加进度不会破坏技术输入哈希。\n"
        "38个场景为 DESIGN_ONLY_NOT_EXECUTED，必须转成真实后端/浏览器测试才形成 P5 证据。\n"
    )
    with (output / "README.md").open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(readme)
    artifacts = [
        {"path": path.relative_to(output).as_posix(), "sha256": sha256(path)}
        for path in sorted(output.rglob("*"))
        if path.is_file()
    ]
    write_new(
        output / "manifest.json",
        {"schema_version": "p5-preparation-1", "kind": "PREPARATION_ONLY", "artifacts": artifacts},
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    try:
        prepare(args.output)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f"P5 准备失败：{exc}")
        return 1
    print(f"P5 准备文件已生成：{args.output}；请运行 check_p5_preparation 进行实际检查")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
