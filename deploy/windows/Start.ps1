param([int]$Port = 8000, [string]$BindAddress = '127.0.0.1', [string]$StateDirectory)
$ErrorActionPreference = 'Stop'
$deliveryRoot = Split-Path -Parent $PSScriptRoot
if (-not $StateDirectory) { $StateDirectory = Join-Path $deliveryRoot 'state' }
$StateDirectory = [IO.Path]::GetFullPath($StateDirectory)
$deliveryPython = Join-Path $deliveryRoot 'environment/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $deliveryPython)) { throw '请先执行 windows/Install.ps1' }
& $deliveryPython -X utf8 (Join-Path $deliveryRoot 'scripts/package_release.py') --verify $deliveryRoot
if ($LASTEXITCODE -ne 0) { throw '部署包文件校验失败' }
New-Item -ItemType Directory -Path $StateDirectory -Force | Out-Null
$deliveryPidFile = Join-Path $StateDirectory 'api-process.json'
if (Test-Path -LiteralPath $deliveryPidFile) {
    $previous = Get-Content -LiteralPath $deliveryPidFile -Raw | ConvertFrom-Json
    $existing = Get-Process -Id $previous.pid -ErrorAction SilentlyContinue
    if ($existing -and $existing.StartTime.ToUniversalTime().Ticks -eq $previous.start_ticks) { throw '该状态目录的 API 进程仍在运行' }
}
$deliveryManifest = Get-Content -LiteralPath (Join-Path $deliveryRoot 'release_manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$env:PYTHONUTF8 = '1'
$env:SMART_COOKING_RELEASE_ROOT = Join-Path $deliveryRoot 'knowledge'
$env:SMART_COOKING_RELEASE_ID = $deliveryManifest.release.release_id
$env:SMART_COOKING_POLICY_PATH = Join-Path $deliveryRoot 'policy.json'
$env:SMART_COOKING_DATABASE_PATH = Join-Path $StateDirectory 'runtime.sqlite3'
$env:SMART_COOKING_FRONTEND_PATH = Join-Path $deliveryRoot 'web/dist'
$env:SMART_COOKING_LANGUAGE_ARCHIVE_PATH = Join-Path $StateDirectory 'intent-runs'
$env:SMART_COOKING_LANGUAGE_ENABLED = '0'
$env:SMART_COOKING_TIMEZONE = 'Asia/Shanghai'
$deliveryProcess = Start-Process -FilePath $deliveryPython -ArgumentList @('-X','utf8','-m','uvicorn','app.main:create_app','--factory','--host',$BindAddress,'--port',"$Port") -WorkingDirectory $deliveryRoot -RedirectStandardOutput (Join-Path $StateDirectory 'api-out.log') -RedirectStandardError (Join-Path $StateDirectory 'api-error.log') -PassThru -WindowStyle Hidden
$deliveryProcess.Refresh()
@{ pid = $deliveryProcess.Id; start_ticks = $deliveryProcess.StartTime.ToUniversalTime().Ticks; python = $deliveryPython; port = $Port; address = $BindAddress; state_directory = $StateDirectory } | ConvertTo-Json | Set-Content -LiteralPath $deliveryPidFile -Encoding UTF8
Write-Output "API 已启动，PID=$($deliveryProcess.Id)，地址=http://${BindAddress}:${Port}；以 /health/ready 确认就绪"
