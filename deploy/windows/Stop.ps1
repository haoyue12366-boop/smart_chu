param([string]$StateDirectory)
$ErrorActionPreference = 'Stop'
$deliveryRoot = Split-Path -Parent $PSScriptRoot
if (-not $StateDirectory) { $StateDirectory = Join-Path $deliveryRoot 'state' }
$deliveryPidFile = Join-Path $StateDirectory 'api-process.json'
if (-not (Test-Path -LiteralPath $deliveryPidFile)) { throw '未找到此状态目录的进程记录' }
$record = Get-Content -LiteralPath $deliveryPidFile -Raw | ConvertFrom-Json
$process = Get-Process -Id $record.pid -ErrorAction SilentlyContinue
if (-not $process) { Write-Output '记录的 API 进程已退出'; return }
if ($process.StartTime.ToUniversalTime().Ticks -ne $record.start_ticks -or $process.Path -ne $record.python) { throw 'PID 身份改变，拒绝停止无关进程' }
& taskkill.exe /PID $record.pid /T /F
if ($LASTEXITCODE -ne 0) { throw 'API 进程树停止失败' }
Write-Output 'API 与求解子进程已停止；已提交数据库保留'
