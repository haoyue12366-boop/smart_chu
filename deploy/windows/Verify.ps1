param([int]$Port = 8000)
$ErrorActionPreference = 'Stop'
$deliveryRoot = Split-Path -Parent $PSScriptRoot
& (Join-Path $deliveryRoot 'runtime/python/python.exe') -X utf8 (Join-Path $deliveryRoot 'scripts/package_release.py') --verify $deliveryRoot
if ($LASTEXITCODE -ne 0) { throw '部署包文件校验失败' }
$response = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health/ready" -TimeoutSec 20
if ($response.status -ne 'ready') { throw '在线服务尚未就绪' }
Write-Output '文件身份与本机服务就绪检查通过'
