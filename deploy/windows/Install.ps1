param()
$ErrorActionPreference = 'Stop'
$deliveryRoot = Split-Path -Parent $PSScriptRoot
& (Join-Path $deliveryRoot 'runtime/python/python.exe') -X utf8 (Join-Path $deliveryRoot 'scripts/package_release.py') --verify $deliveryRoot
if ($LASTEXITCODE -ne 0) { throw '部署包文件校验失败' }
$deliveryUv = Join-Path $deliveryRoot 'tools/uv.exe'
$deliveryPython = Join-Path $deliveryRoot 'runtime/python/python.exe'
$deliveryEnvironment = Join-Path $deliveryRoot 'environment'
$env:UV_CACHE_DIR = Join-Path $deliveryRoot 'install-cache'
if (Test-Path -LiteralPath $deliveryEnvironment) { throw '安装目录已有 environment，请使用独立的新目录安装' }
& $deliveryUv venv --offline --python $deliveryPython $deliveryEnvironment
if ($LASTEXITCODE -ne 0) { throw '离线创建虚拟环境失败' }
& $deliveryUv pip install --offline --no-index --require-hashes --find-links (Join-Path $deliveryRoot 'wheels') --python (Join-Path $deliveryEnvironment 'Scripts/python.exe') -r (Join-Path $deliveryRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw '锁定依赖离线安装失败' }
Get-ChildItem -LiteralPath (Join-Path $deliveryRoot 'knowledge') -File -Recurse | ForEach-Object { $_.IsReadOnly = $true }
Write-Output 'Windows 离线安装完成'
