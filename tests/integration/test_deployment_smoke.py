"""独立 Windows 目录的真实离线安装、HTTP 进程与重启恢复。"""

import json
import os
import shutil
import socket
import subprocess
import time
import zipfile
from pathlib import Path

import httpx

from scripts.package_release import verify_delivery

ROOT = Path(__file__).resolve().parents[2]
STEAK = {"id": "61e6c51fec6e1d65587067e1", "name": "低温牛排"}
ADDED = {"id": "5c8200d96dc6e123a037a202", "name": "豆豉蒸腐竹"}


def test_windows_offline_package_real_start_replan_and_restart(tmp_path, record_testsuite_property):
    assert os.name == "nt", "此验收要求真实 Windows，不能用其他平台跳过充当通过"
    archive = Path(
        os.getenv(
            "SMART_COOKING_P6_DELIVERY",
            str(ROOT / "data/delivery/SmartCooking-Windows-20261007-PS51-fix.zip"),
        )
    )
    assert archive.is_file(), "必须先实际生成固定 Windows 部署 ZIP"
    shell = (
        os.getenv("SMART_COOKING_DELIVERY_SHELL")
        or shutil.which("powershell")
        or shutil.which("pwsh")
    )
    assert shell, "Windows PowerShell 不可用"
    shell_version = subprocess.check_output(
        [
            shell,
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "$PSVersionTable.PSVersion.ToString()",
        ],
        text=True,
        timeout=30,
    ).strip()
    directory = tmp_path / "独立安装目录"
    directory.mkdir()
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(directory)
    identity = verify_delivery(directory)
    manifest = json.loads((directory / "release_manifest.json").read_bytes())
    assert manifest["platform"] == "Windows AMD64"
    assert manifest["wheel_count"] > 0 and not manifest["network_required_for_install"]
    with socket.socket() as selected:
        selected.bind(("127.0.0.1", 0))
        port = selected.getsockname()[1]
    records = []

    def command(name, *args):
        log = tmp_path / f"command-{len(records)}.log"
        with log.open("wb") as stream:
            result = subprocess.run(
                [
                    shell,
                    "-NoProfile",
                    "-NonInteractive",
                    "-File",
                    str(directory / "windows" / name),
                    *map(str, args),
                ],
                cwd=directory,
                stdout=stream,
                stderr=subprocess.STDOUT,
                timeout=180,
                env={**os.environ, "PYTHONUTF8": "1", "PIP_NO_INDEX": "1", "UV_OFFLINE": "1"},
            )
        records.append(
            {
                "script": name,
                "exit_code": result.returncode,
                "output": log.read_text(encoding="utf-8", errors="replace"),
            }
        )
        assert result.returncode == 0, records[-1]

    def ready():
        until = time.monotonic() + 40
        while time.monotonic() < until:
            try:
                response = httpx.get(
                    f"http://127.0.0.1:{port}/health/ready", timeout=1, trust_env=False
                )
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        log = directory / "state/api-error.log"
        raise AssertionError(log.read_text(encoding="utf-8") if log.exists() else "API 未就绪")

    try:
        command("Install.ps1")
        command("Start.ps1", "-Port", port)
        ready()
        command("Verify.ps1", "-Port", port)
        with httpx.Client(
            base_url=f"http://127.0.0.1:{port}", timeout=20, trust_env=False
        ) as client:
            url = "/api/competition/plan?task_id=p6-windows"
            initial = client.post(url, json=[STEAK], headers={"Idempotency-Key": "initial"})
            assert initial.status_code == 200, initial.text
            added = client.post(url, json=[ADDED], headers={"Idempotency-Key": "add"})
            assert added.status_code == 200, added.text
            assert added.json()["overview"]["recipeCount"] == 2
            context = client.get("/api/v1/competition-tasks/p6-windows").json()
            sid = context["session_id"]
            before = client.get(f"/api/v1/sessions/{sid}").json()
            assert before["runtime"]["current_plan_version"] == 2
            assert before["runtime"]["executions"] == []
            assert client.get("/").status_code == 200
        command("Stop.ps1")
        command("Start.ps1", "-Port", port)
        ready()
        with httpx.Client(
            base_url=f"http://127.0.0.1:{port}", timeout=20, trust_env=False
        ) as client:
            assert client.get(f"/api/v1/sessions/{sid}").json() == before
            replay = client.post(url, json=[ADDED], headers={"Idempotency-Key": "add"})
            assert replay.text == added.text
            assert client.get(f"/api/v1/sessions/{sid}").json() == before
            assert client.get("/api/v1/competition-tasks/p6-windows").json() == context
        assert verify_delivery(directory) == identity
    finally:
        if (directory / "state/api-process.json").exists():
            command("Stop.ps1")
        (tmp_path / "windows-deployment-evidence.json").write_text(
            json.dumps(
                {
                    "identity": identity,
                    "scope": manifest["kind"],
                    "shell": shell,
                    "shell_version": shell_version,
                    "commands": records,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        for key, value in identity.items():
            record_testsuite_property("windows_deployment_" + key, str(value))
        record_testsuite_property("windows_deployment_shell_version", shell_version)
