#Requires -Version 5.1
<#
.SYNOPSIS
    下载并校验「弈心 · 围棋陪练」需要的外部文件（KataGo 神经网络模型，可选整个引擎）。

.DESCRIPTION
    仓库不把引擎和模型提交进 git（体积大，见 .gitignore）。发布包里已经带好；
    只有从源码运行、或者换了机器时才需要这个脚本。

    两个神经网络来自 KataGo 官方 GitHub Release，下载到 models\ 并校验 SHA256：

        b10c512h8nbt3tflrs-fson-silu-rsnh.bin.gz   KataGo v1.17.1   主模型（职业档）
        b18c384nbt-humanv0.bin.gz                  KataGo v1.15.0   Human SL（15级–业余3段）

    引擎目录 engine\katago-opencl\ 的来源是 KataGo v1.18.1 官方 release：

        katago-v1.18.1-opencl-windows-x64.zip

    仓库里现有的 engine\katago-opencl\ 就是把这个 zip 解压进去，再加上本机生成的
    KataGoData\opencltuning\ 调优缓存。加 -IncludeEngine 可以让脚本顺带下载并解压它。

    已存在且 SHA256 正确的文件会跳过；curl.exe 可用时用 `curl -C -` 断点续传，
    中途失败可以重跑。校验失败的文件会保留为 *.bad 供排查，不会静默覆盖。

.PARAMETER ModelsDir
    模型保存目录，默认仓库根目录的 models\。

.PARAMETER IncludeEngine
    额外下载 KataGo 官方 OpenCL 引擎 zip，并解压到 engine\katago-opencl\。
    已有引擎时不需要。

.PARAMETER EngineDir
    引擎解压目标目录，默认 engine\katago-opencl\。

.PARAMETER VerifyOnly
    只校验已存在的文件（不发网络请求），缺失或哈希不符时退出码为 1。
    断网环境手动下载模型后，可以用它确认文件是否正确。

.PARAMETER Force
    已有文件校验不通过时，重新下载并覆盖（旧文件改名为 *.bad 保留）。

.PARAMETER ReleaseBaseUrl
    Release 下载地址前缀，默认 KataGo 官方地址。如果所在网络访问不了 GitHub，
    可换成你信任的镜像地址；文件仍然会按内置 SHA256 严格校验，镜像内容不对就会拒绝。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\download-models.ps1 -WhatIf

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\download-models.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\download-models.ps1 -VerifyOnly

.NOTES
    网络：脚本直接请求 github.com 的 release 下载地址，curl.exe 会遵循
    HTTP_PROXY / HTTPS_PROXY 环境变量。连不上 GitHub 时，从 release 页面手动
    下载两个 .bin.gz 放进 models\，再用 -VerifyOnly 校验即可。
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$ModelsDir,
    [string]$EngineDir,
    [switch]$IncludeEngine,
    [switch]$VerifyOnly,
    [switch]$Force,
    [string]$ReleaseBaseUrl = "https://github.com/lightvector/KataGo/releases/download"
)

$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false) } catch { }

# -WhatIf：记录下来后立刻关掉 $WhatIfPreference 的传播，否则脚本里 Get-FileHash、
# Get-Item 这类内部调用也会被当成"预演"而不真正读文件，后面取哈希就会报空值。
$DryRun = [bool]$WhatIfPreference
$WhatIfPreference = $false

$RepoRoot = Split-Path -Parent $PSScriptRoot
if (-not $RepoRoot) { $RepoRoot = (Get-Location).Path }
if (-not $ModelsDir) { $ModelsDir = Join-Path $RepoRoot "models" }
if (-not $EngineDir) { $EngineDir = Join-Path $RepoRoot "engine\katago-opencl" }
# 引擎 zip 下载到引擎目录旁边（默认就是仓库的 engine\）
$EngineDownloadDir = Split-Path -Parent $EngineDir

# 官方 release 里的文件清单。大小和 SHA256 都是和 KataGo 官方 release 资产核对过的，
# 不要凭感觉改；要换模型版本请同时更新这里和 README / THIRD_PARTY_NOTICES.md。
$Targets = @(
    [pscustomobject]@{
        Name   = "b10c512h8nbt3tflrs-fson-silu-rsnh.bin.gz"
        Kind   = "model"
        Tag    = "v1.17.1"
        Size   = 94281753
        Sha256 = "c04db4a503721d948bb720324f3cbdac6088cc9eb243632f020e4b6846f58995"
        Note   = "主模型：职业2段 / 职业5段 / 顶尖职业档"
    },
    [pscustomobject]@{
        Name   = "b18c384nbt-humanv0.bin.gz"
        Kind   = "model"
        Tag    = "v1.15.0"
        Size   = 99066230
        Sha256 = "637746e44f0efe00ad1245a50aa9bbf0716efe364c43965ead97bd6835d84ab5"
        Note   = "Human SL 模型：15级 / 10级 / 5级 / 1级 / 业余1段 / 业余3段档"
    },
    [pscustomobject]@{
        Name   = "katago-v1.18.1-opencl-windows-x64.zip"
        Kind   = "engine"
        Tag    = "v1.18.1"
        Size   = 6004137
        Sha256 = "1710db1903ab921aa6837a9599c8474f8a59f057650217c5d9bc125ee393a9ff"
        Note   = "KataGo 1.18.1 OpenCL Windows x64 引擎"
    }
)

function Write-Section([string]$Text) {
    Write-Host ""
    Write-Host ("== " + $Text) -ForegroundColor Cyan
}

function Get-TargetDestination($Target) {
    if ($Target.Kind -eq "model") { return (Join-Path $ModelsDir $Target.Name) }
    return (Join-Path $EngineDownloadDir $Target.Name)
}

function Get-TargetUrl($Target) {
    return ($ReleaseBaseUrl.TrimEnd("/") + "/" + $Target.Tag + "/" + $Target.Name)
}

function Get-Sha256Hex([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Test-TargetFile($Target) {
    # 返回 ok / missing / corrupt
    $dest = Get-TargetDestination $Target
    if (-not (Test-Path -LiteralPath $dest -PathType Leaf)) { return "missing" }
    $len = (Get-Item -LiteralPath $dest).Length
    if ($len -ne $Target.Size) { return "corrupt" }
    if ((Get-Sha256Hex $dest) -ne $Target.Sha256) { return "corrupt" }
    return "ok"
}

function Write-DownloadPlan($Target) {
    $dest = Get-TargetDestination $Target
    Write-Host ("  用途：" + $Target.Note)
    Write-Host ("  保存：" + $dest)
    Write-Host ("  地址：" + (Get-TargetUrl $Target))
    Write-Host ("  大小：" + $Target.Size + " 字节")
    Write-Host ("  SHA256：" + $Target.Sha256)
    $part = $dest + ".part"
    if (Test-Path -LiteralPath $part) {
        Write-Host ("  发现未完成的临时文件 " + $part + "，curl 会尝试断点续传") -ForegroundColor Yellow
    }
}

function Invoke-TargetDownload($Target) {
    $dest = Get-TargetDestination $Target
    $dir = Split-Path -Parent $dest
    if (-not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    $url = Get-TargetUrl $Target
    $part = $dest + ".part"

    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($curl) {
        $curlArgs = @()
        if (Test-Path -LiteralPath $part) { $curlArgs += @("-C", "-") }
        $curlArgs += @("-L", "--fail", "--retry", "3", "--retry-delay", "2", "--connect-timeout", "20", "-o", $part, $url)
        Write-Host ("  下载中：curl.exe " + ($curlArgs -join " "))
        & $curl.Source @curlArgs
        if ($LASTEXITCODE -ne 0) { throw ("curl 下载失败，退出码 " + $LASTEXITCODE + "。网络不稳定时可以直接重跑本脚本续传。") }
    }
    else {
        Write-Warning "没找到 curl.exe（Windows 10 1803 以前才有这个问题），改用 Invoke-WebRequest，不支持断点续传。"
        Invoke-WebRequest -Uri $url -OutFile $part -UseBasicParsing
    }

    if (-not (Test-Path -LiteralPath $part -PathType Leaf)) { throw ("下载结束后没有找到文件：" + $part) }
    $len = (Get-Item -LiteralPath $part).Length
    if ($len -ne $Target.Size) {
        throw ("文件大小不对：期望 " + $Target.Size + " 字节，实际 " + $len + "。" + $part + " 保留着，可重跑续传。")
    }
    $actual = Get-Sha256Hex $part
    if ($actual -ne $Target.Sha256) {
        $bad = $part + ".bad"
        Move-Item -LiteralPath $part -Destination $bad -Force
        throw ("SHA256 校验失败：期望 " + $Target.Sha256 + "，实际 " + $actual + "。文件已改名为 " + $bad + "，请检查网络/代理是否篡改了下载内容。")
    }
    Move-Item -LiteralPath $part -Destination $dest -Force
    Write-Host "  SHA256 校验通过，已保存。" -ForegroundColor Green
}

function Invoke-VerifyOnly {
    Write-Section "只校验已有文件（不发网络请求）"
    $allOk = $true
    $checkTargets = @($Targets | Where-Object { $_.Kind -eq "model" })
    foreach ($t in $checkTargets) {
        $dest = Get-TargetDestination $t
        $state = Test-TargetFile $t
        if ($state -eq "ok") {
            Write-Host ("  [OK]   " + $dest) -ForegroundColor Green
            continue
        }
        if ($state -eq "missing") {
            Write-Host ("  [缺失] " + $dest) -ForegroundColor Red
        }
        else {
            $len = (Get-Item -LiteralPath $dest).Length
            $actual = Get-Sha256Hex $dest
            Write-Host ("  [哈希不符] " + $dest) -ForegroundColor Red
            Write-Host ("             期望 " + $t.Sha256 + " / " + $t.Size + " 字节")
            Write-Host ("             实际 " + $actual + " / " + $len + " 字节")
        }
        $allOk = $false
    }
    if ($IncludeEngine) {
        $exe = Join-Path $EngineDir "katago.exe"
        if (Test-Path -LiteralPath $exe -PathType Leaf) {
            Write-Host ("  [OK]   " + $exe) -ForegroundColor Green
            $zip = Get-TargetDestination ($Targets | Where-Object { $_.Kind -eq "engine" })
            if (Test-Path -LiteralPath $zip -PathType Leaf) {
                Write-Host ("  [OK]   " + $zip) -ForegroundColor Green
            }
        }
        else {
            Write-Host ("  [缺失] " + $exe + "（可加 -IncludeEngine 下载引擎，或手动从 KataGo release 解压）") -ForegroundColor Red
            $allOk = $false
        }
    }
    Write-Host ""
    if ($allOk) {
        Write-Host "全部校验通过。" -ForegroundColor Green
        exit 0
    }
    Write-Host "校验未通过。修复后用 -VerifyOnly 重跑。" -ForegroundColor Red
    exit 1
}

# ============================== 主流程 ==============================
Write-Section "弈心 · 围棋陪练：外部文件下载 / 校验"
Write-Host ("仓库根目录：" + $RepoRoot)
Write-Host ("模型目录：" + $ModelsDir)
if ($IncludeEngine) { Write-Host ("引擎目录：" + $EngineDir) }

if ($VerifyOnly) { Invoke-VerifyOnly }

$modelTargets = @($Targets | Where-Object { $_.Kind -eq "model" })
foreach ($t in $modelTargets) {
    Write-Section ("模型 " + $t.Name)
    $state = Test-TargetFile $t
    if ($state -eq "ok") {
        Write-Host "  已存在且 SHA256 校验通过，跳过。" -ForegroundColor Green
        continue
    }
    if ($state -eq "corrupt") {
        $dest = Get-TargetDestination $t
        if (-not $Force) {
            throw ("已有文件校验不通过：" + $dest + "`n用 -Force 重新下载（旧文件会改名为 .bad 保留），或先手动删掉它。")
        }
        $bad = $dest + ".bad"
        Write-Warning ("旧文件校验不通过，改名保留：" + $bad)
        if (-not $DryRun) { Move-Item -LiteralPath $dest -Destination $bad -Force }
    }
    Write-DownloadPlan $t
    if ($DryRun) {
        Write-Host "  [WhatIf] 不发起下载、不写文件。" -ForegroundColor Yellow
        continue
    }
    Invoke-TargetDownload $t
}

if ($IncludeEngine) {
    $engineTarget = $Targets | Where-Object { $_.Kind -eq "engine" }
    $engineExe = Join-Path $EngineDir "katago.exe"
    Write-Section ("引擎 " + $engineTarget.Name)
    if ((Test-Path -LiteralPath $engineExe -PathType Leaf) -and (-not $Force)) {
        Write-Host ("  已检测到现有引擎：" + $engineExe) -ForegroundColor Green
        Write-Host "  跳过引擎下载。要重新下载并覆盖，加 -Force。"
    }
    else {
        $state = Test-TargetFile $engineTarget
        if ($state -eq "ok") {
            Write-Host "  安装包已存在且 SHA256 校验通过，跳过下载。" -ForegroundColor Green
        }
        else {
            if ($state -eq "corrupt" -and -not $Force) {
                throw ("已有文件校验不通过：" + (Get-TargetDestination $engineTarget) + "`n用 -Force 重新下载。")
            }
            Write-DownloadPlan $engineTarget
            if ($DryRun) {
                Write-Host "  [WhatIf] 不发起下载、不解压。" -ForegroundColor Yellow
            }
            else {
                Invoke-TargetDownload $engineTarget
                if (-not (Test-Path -LiteralPath $EngineDir)) {
                    New-Item -ItemType Directory -Path $EngineDir -Force | Out-Null
                }
                Write-Host ("  解压到：" + $EngineDir)
                Expand-Archive -LiteralPath (Get-TargetDestination $engineTarget) -DestinationPath $EngineDir -Force
                if (Test-Path -LiteralPath $engineExe -PathType Leaf) {
                    Write-Host "  katago.exe 已就位。" -ForegroundColor Green
                    Write-Host "  提示：本机首次用某个模型 × 显卡组合时，KataGo 会做一次 OpenCL 调优，" -ForegroundColor Yellow
                    Write-Host "        缓存写到 engine\katago-opencl\KataGoData\opencltuning\，之后启动会快很多。" -ForegroundColor Yellow
                }
                else {
                    Write-Warning ("解压后没找到 " + $engineExe + "。请检查 zip 目录结构，手动把 katago.exe 和 DLL 放到该目录。")
                }
            }
        }
    }
}
else {
    Write-Section "引擎（未下载）"
    Write-Host "  engine\katago-opencl\ 的来源：KataGo v1.18.1 官方 release 的"
    Write-Host "  katago-v1.18.1-opencl-windows-x64.zip。解压后把 katago.exe 与 DLL 放进该目录即可。"
    Write-Host "  发布包里已经带好；已有引擎时不需要重复下载。要由脚本下载，加 -IncludeEngine。"
}

Write-Section "完成"
Write-Host "下一步：python server.py --no-browser"
Write-Host "复核哈希：powershell -ExecutionPolicy Bypass -File scripts\download-models.ps1 -VerifyOnly"
Write-Host "注意：再分发随包的 KataGo 引擎/模型时，请保留上游版权与许可声明；"
Write-Host "      KataGo v1.18.1 的 LICENSE 为 MIT 正文（另附第三方组件与免责声明），"
Write-Host "      两个模型的来源与版本见 THIRD_PARTY_NOTICES.md 和上游 release 页。"
exit 0
