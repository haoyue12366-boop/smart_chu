"""真实 Node 工具链验收辅助；缺工具或空/跳过的测试均失败，不模拟后端。"""

import json
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def npm_command() -> list[str]:
    node = shutil.which("node")
    if node is None:
        raise RuntimeError("P5 验收需要实际 Node 工具链")
    cli = Path(node).parent / "node_modules/npm/bin/npm-cli.js"
    if not cli.is_file():
        raise RuntimeError("缺少同一 Node 安装内的 npm CLI")
    return [node, str(cli), "--prefix", "web"]


def run_web(args: list[str], *, env: dict[str, str] | None = None) -> dict[str, object]:
    command = [*npm_command(), *args]
    start = time.monotonic()
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=240,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "Traceback (most recent call last)" not in output, output
    assert "database is locked" not in output, output
    return {
        "command": command,
        "exit_code": result.returncode,
        "elapsed_ms": round((time.monotonic() - start) * 1000),
        "output": output,
    }


def checked_cases(junit: Path) -> dict[str, int]:
    cases = ET.parse(junit).getroot().findall(".//testcase")
    assert cases, "前端验收不能接受空测试集"
    failed = sum(c.find("failure") is not None or c.find("error") is not None for c in cases)
    skipped = sum(c.find("skipped") is not None for c in cases)
    assert failed == skipped == 0, junit.read_text(encoding="utf-8")
    return {"passed": len(cases), "failed": failed, "skipped": skipped}


def record_web(property_writer, name: str, commands: list[dict[str, object]]) -> None:
    property_writer(name, json.dumps(commands, ensure_ascii=False))
