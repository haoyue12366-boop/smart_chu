"""部署脚本必须能被系统自带的 Windows PowerShell 5.1 正确解析。"""

import base64
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("name", ["Install.ps1", "Start.ps1", "Stop.ps1", "Verify.ps1"])
def test_delivery_script_parses_with_windows_powershell_51(name):
    assert os.name == "nt", "必须使用真实 Windows PowerShell 验证"
    shell = shutil.which("powershell.exe")
    assert shell, "系统 Windows PowerShell 不可用"
    path = str(ROOT / "deploy/windows" / name).replace("'", "''")
    probe = f"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$parseTokens = $null
$parseErrors = $null
[System.Management.Automation.Language.Parser]::ParseFile(
    '{path}', [ref]$parseTokens, [ref]$parseErrors
) | Out-Null
@{{
    version = $PSVersionTable.PSVersion.ToString()
    errors = @($parseErrors | ForEach-Object {{ $_.ErrorId }})
}} | ConvertTo-Json -Compress
"""
    encoded = base64.b64encode(probe.encode("utf-16-le")).decode("ascii")
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    report = json.loads(result.stdout.decode("utf-8-sig"))
    assert report["version"].startswith("5.1."), report
    assert report["errors"] == [], {"script": name, **report}
