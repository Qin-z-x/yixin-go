# 一条命令完成发布（token 就绪后直接跑）
#
# 前置：已经完成设备授权（python scripts\gh-auth.py --poll 600），
#       token 落在 %USERPROFILE%\.dsh\gh-token.txt，或者设置了 $env:GITHUB_TOKEN。
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File scripts\publish-now.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\publish-now.ps1 -Repo my-go-app -Tag v0.1.0 -DryRun

[CmdletBinding()]
param(
    [string]$Repo = "yixin-go",
    [string]$Tag = "v0.1.0",
    [string]$Owner = "",
    [string]$Asset = "dist\Yixin-Go-Sparring-0.1.0-portable.zip",
    [string]$Notes = "docs\RELEASE-NOTES-0.1.0.md",
    [switch]$DryRun
)

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

$py = "C:\Users\28643\AppData\Local\Programs\Python\Python313-32\python.exe"
if (-not (Test-Path $py)) { $py = (Get-Command python -ErrorAction SilentlyContinue).Source }
if (-not $py) { throw "找不到 Python 解释器" }

$gitExe = "C:\Users\28643\Downloads\tools\mingit\cmd\git.exe"
if (-not (Test-Path $gitExe)) { $gitExe = (Get-Command git -ErrorAction SilentlyContinue).Source }

$tokenFile = Join-Path $env:USERPROFILE ".dsh\gh-token.txt"
if (-not $env:GITHUB_TOKEN -and -not (Test-Path $tokenFile)) {
    Write-Host "还没有 token。先完成设备授权：" -ForegroundColor Yellow
    Write-Host "  $py scripts\gh-auth.py --poll 600"
    exit 1
}
if (-not (Test-Path $Asset)) { throw "找不到发布资产：$Asset" }

$args = @("scripts\publish.py", "--repo", $Repo, "--tag", $Tag,
          "--asset", $Asset, "--notes-file", $Notes)
if ($Owner) { $args += @("--owner", $Owner) }
if ($gitExe) { $args += @("--git", $gitExe) }
if ($DryRun) { $args += "--dry-run" }
elseif (Test-Path $tokenFile -and -not $env:GITHUB_TOKEN) { $args += @("--token-file", $tokenFile) }

Write-Host "执行：python $($args -join ' ')" -ForegroundColor Cyan
& $py @args
exit $LASTEXITCODE
