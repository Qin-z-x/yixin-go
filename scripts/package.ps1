# 打便携版分发包
#
# 前置：已经用 PyInstaller 构建过（产物在 dist\弈心围棋陪练\），或者本机有完整源码树。
# 本脚本只做"组装 + 校验和"，不做编译。
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File scripts\package.ps1 -Version 0.1.0
#   powershell -ExecutionPolicy Bypass -File scripts\package.ps1 -Version 0.1.0 -Source dist\弈心围棋陪练 -WhatIf
#
# 产物：
#   dist\Yixin-Go-Sparring-<version>-portable.zip
#   dist\SHA256SUMS.txt

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Version,

    [string]$Source = "",
    [string]$OutDir = "",
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
if (-not $Source) { $Source = Join-Path $root "dist\弈心围棋陪练" }
if (-not $OutDir) { $OutDir = Join-Path $root "dist" }

function Write-Step($msg) { Write-Host "`n== $msg" -ForegroundColor Cyan }
function Write-Warn2($msg) { Write-Host "   $msg" -ForegroundColor Yellow }

Write-Step "打包便携版 $Version"
Write-Host "仓库根目录：$root"
Write-Host "源目录：    $Source"
Write-Host "输出目录：  $OutDir"

if (-not (Test-Path $Source)) {
    throw "找不到源目录：$Source。请先运行 PyInstaller：python -m PyInstaller 围棋陪练.spec --noconfirm"
}

# 必带文件
$required = @("围棋陪练.exe", "web", "configs", "engine", "models")
$missing = @()
foreach ($item in $required) {
    if (-not (Test-Path (Join-Path $Source $item))) { $missing += $item }
}
if ($missing.Count -gt 0) {
    Write-Warn2 "源目录缺少：$($missing -join ', ')"
    if ($missing -contains "engine" -or $missing -contains "models") {
        throw "缺少引擎或模型，打出来的包不能直接玩。先跑 scripts\download-models.ps1 或从现有 dist 目录复制。"
    }
}

# 必须排除的运行期产物（隐私 + 体积）
$excludeRelative = @("logs", "data\games", "data\edge-preview", "data\browser-profile", "logs\*.log", "*.log", "*.pyc", "__pycache__")
Write-Step "排除运行期产物"
foreach ($rel in $excludeRelative) { Write-Warn2 "exclude: $rel" }

$safeName = "Yixin-Go-Sparring-$Version-portable"
$zipPath = Join-Path $OutDir "$safeName.zip"
$sumPath = Join-Path $OutDir "SHA256SUMS.txt"

if ($WhatIf) {
    Write-Step "[WhatIf] 将执行"
    Write-Host "  robocopy `"$Source`" `"<staging>\\$safeName`" /E /XD logs data\\edge-preview __pycache__"
    Write-Host "  Compress-Archive -> $zipPath"
    Write-Host "  Get-FileHash -> $sumPath"
    return
}

Write-Step "复制到暂存目录（排除 logs / data 运行期产物）"
$stagingRoot = Join-Path $OutDir "_package"
$staging = Join-Path $stagingRoot $safeName
if (Test-Path $stagingRoot) { Remove-Item -Recurse -Force $stagingRoot }
New-Item -ItemType Directory -Force -Path $staging | Out-Null

$robolog = & robocopy $Source $staging /E /NFL /NDL /NJH /NJS /NP `
    /XD logs data\games data\edge-preview data\browser-profile __pycache__ .git `
    /XF *.log *.pyc /R:1 /W:1 2>&1
if ($LASTEXITCODE -ge 8) { throw "robocopy 失败（exit $LASTEXITCODE）：$robolog" }

Write-Step "补齐说明与许可文件"
foreach ($doc in @("README.md", "THIRD_PARTY_NOTICES.md", "LICENSE", "CHANGELOG.md")) {
    $src = Join-Path $root $doc
    if (Test-Path $src) { Copy-Item $src -Destination $staging -Force }
}
$katagoLic = Join-Path $root "third_party\KataGo-LICENSE.txt"
if (Test-Path $katagoLic) {
    New-Item -ItemType Directory -Force -Path (Join-Path $staging "third_party") | Out-Null
    Copy-Item $katagoLic -Destination (Join-Path $staging "third_party") -Force
}

# 隐私检查：不允许出现本机绝对路径
Write-Step "隐私检查"
$leaks = Get-ChildItem $staging -Recurse -File -Include *.log,*.txt,*.json,*.md,*.cfg -ErrorAction SilentlyContinue |
    Select-String -Pattern 'C:\\Users\\|/Users/' -ErrorAction SilentlyContinue
if ($leaks) {
    $leaks | Select-Object -First 5 | ForEach-Object { Write-Warn2 "$($_.Path):$($_.LineNumber) $($_.Line.Trim())" }
    throw "暂存目录里发现本机绝对路径，已中止打包。"
}
Write-Host "   未发现绝对路径泄露" -ForegroundColor Green

Write-Step "压缩"
if (Test-Path $zipPath) { Remove-Item -Force $zipPath }
# 用 .NET 压缩以保留中文文件名与 UTF-8 标志位
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory($staging, $zipPath, [System.IO.Compression.CompressionLevel]::Optimal, $true)

Write-Step "校验和"
$hash = (Get-FileHash $zipPath -Algorithm SHA256).Hash.ToLower()
$name = Split-Path $zipPath -Leaf
"$hash  $name" | Out-File $sumPath -Encoding ascii
Write-Host "   $hash  $name" -ForegroundColor Green

$sizeMb = [math]::Round((Get-Item $zipPath).Length / 1MB, 1)
Write-Step "完成"
Write-Host "  包：$zipPath（$sizeMb MB）"
Write-Host "  校验：$sumPath"
Write-Host ""
Write-Host "发布前人工确认："
Write-Host "  1) 解压到不含中文的路径，双击 启动围棋陪练.bat 能出棋盘"
Write-Host "  2) 断网状态下能对局与复盘"
Write-Host "  3) 一台没装 Python 的机器上再验一次"
