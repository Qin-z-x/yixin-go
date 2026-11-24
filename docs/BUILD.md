# 从源码运行与构建

这份文档讲清楚三件事：**怎么从源码把服务跑起来**、**引擎和模型从哪来**、**怎么打出 Windows 便携版**。
面向的是想改代码、想自己打包，或者只想确认发布包里没夹带奇怪东西的人。

## 1. 环境要求

- **Windows 10 / 11 x64**（本机开发环境是 32 位 Python 3.13，64 位 Python 也能跑源码）。
- **Python 3.10 及以上**，只需要标准库，源码运行不需要 `pip install` 任何东西。
- **一块支持 OpenCL 的显卡** + 厂商驱动：NVIDIA / AMD / Intel 核显都可以，NVIDIA 独显最快。
- **磁盘**：源码 + 引擎 + 两个模型约 250 MB；打包目录约 225 MB；构建时另外留约 1 GB 给 `build/` 和 `dist/`。

打包只是一次性构建需求，需要 PyInstaller 6.x（见第 6 节），跟源码运行无关。

## 2. 目录与运行时文件

`server.py` 启动时会确定一个"程序根目录"：源码模式取 `server.py` 所在目录，PyInstaller 打包后取 `围棋陪练.exe` 所在目录。下面这些都在这个根目录下按相对路径查找：

| 路径 | 用途 | 是否进 git |
| --- | --- | --- |
| `server.py` | HTTP 服务、对局状态、复盘调度 | 是 |
| `go_engine.py` / `katago_engine.py` / `levels.py` | 规则、KataGo 封装、强度档位 | 是 |
| `web/` | 无框架前端（`index.html` / `app.js` / `style.css` / `favicon.svg`） | 是 |
| `configs/` | KataGo 分析配置（`analysis_opencl.cfg` 等） | 是 |
| `engine/katago-opencl/` | KataGo 可执行文件与 DLL | 否（体积大） |
| `models/` | 两个 `.bin.gz` 神经网络 | 否（体积大） |
| `data/games/` | 对局自动保存的 JSON / SGF | 否（运行期生成） |
| `logs/` | KataGo 与服务的日志 | 否（运行期生成） |

`.gitignore` 已经忽略 `engine/`、`models/`、`dist/`、`build/`、`data/`、`logs/`，所以克隆下来是一个"缺引擎和模型"的干净仓库，这是故意的。

## 3. 获取引擎与模型

### 3.1 模型：用脚本下载并校验

```powershell
# 先看要下载什么、从哪下、SHA256 是多少（不发请求、不写文件）
powershell -ExecutionPolicy Bypass -File scripts\download-models.ps1 -WhatIf

# 下载到 models\ 并校验
powershell -ExecutionPolicy Bypass -File scripts\download-models.ps1

# 只校验已有文件（断网手动下载后也能用）
powershell -ExecutionPolicy Bypass -File scripts\download-models.ps1 -VerifyOnly
```

脚本内置的清单（来自 KataGo 官方 GitHub Release，字节数与上游资产一致）：

| 文件 | 来源 release | 大小 | SHA256 |
| --- | --- | --- | --- |
| `b10c512h8nbt3tflrs-fson-silu-rsnh.bin.gz` | KataGo `v1.17.1` | 94,281,753 | `c04db4a503721d948bb720324f3cbdac6088cc9eb243632f020e4b6846f58995` |
| `b18c384nbt-humanv0.bin.gz` | KataGo `v1.15.0` | 99,066,230 | `637746e44f0efe00ad1245a50aa9bbf0716efe364c43965ead97bd6835d84ab5` |

脚本用 `curl.exe` 下载（支持 `-C -` 断点续传，重跑即可接着下），没有 `curl` 时退回 `Invoke-WebRequest`；下载完按大小和 SHA256 校验，不通过会留下 `*.bad` 并报错，不会把坏文件当模型用。连不上 GitHub 时，从 [KataGo releases](https://github.com/lightvector/KataGo/releases) 页面手动下载后放进 `models\`，再用 `-VerifyOnly` 核对哈希。

### 3.2 引擎：KataGo v1.18.1 OpenCL 构建

引擎不放进 git。从 KataGo 官方 [v1.18.1 release](https://github.com/lightvector/KataGo/releases/tag/v1.18.1) 下载：

- 文件：`katago-v1.18.1-opencl-windows-x64.zip`
- 大小：6,004,137 字节
- SHA256：`1710db1903ab921aa6837a9599c8474f8a59f057650217c5d9bc125ee393a9ff`

解压到 `engine/katago-opencl/`，该目录里应该有 `katago.exe`、若干 DLL（`z.dll`、`bz2.dll`、`libcrypto-3-x64.dll` 等）、KataGo 自带的示例 `.cfg` 和 `README.txt`。也可以让脚本代劳：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\download-models.ps1 -IncludeEngine
```

这个目录里还有 `KataGoData/opencltuning/`，是本机第一次跑某个模型 × 显卡组合时生成的 OpenCL 调优缓存，删掉会重新调优（变慢），不用提交。

> OpenCL 是 KataGo 官方提供的跨显卡通用后端，官方 release notes 里明确说它比 CUDA / TensorRT 构建慢。有 NVIDIA 显卡又追求速度的人，可以自己换官方 CUDA 构建，把 `katago.exe` 和配套 DLL 放进来，或者用 `--engine-path` 指到别的目录；这里不展开，README 里的耗时数字都是 OpenCL 后端的实测。

### 3.3 确认文件齐全

```powershell
Get-ChildItem engine\katago-opencl\katago.exe, models\*.bin.gz
powershell -ExecutionPolicy Bypass -File scripts\download-models.ps1 -VerifyOnly
```

## 4. 从源码运行

```powershell
python server.py --no-browser
```

启动后控制台会打印实际地址（默认 `http://127.0.0.1:8765`）。页面可以马上打开，AI 引擎在后台预热；首次启动约 10–30 秒，第一次用某个模型 × 显卡组合时可能要做几分钟 OpenCL 调优，之后会快。

命令行参数（`python server.py --help` 为准）：

| 参数 | 说明 |
| --- | --- |
| `--port <端口>` | 首选端口，默认 8765；被占用时自动往后试 19 个端口（最大 8784），控制台打印实际地址 |
| `--no-browser` / `--no-open` | 不自动打开浏览器 |
| `--engine-path <路径>` | KataGo 可执行文件或所在目录，默认自动在 `engine/` 下找 |
| `--models-dir <目录>` | 模型目录，默认 `models/` |
| `--log-level <级别>` | `debug` / `info` / `warning` / `error` / `critical` / `none`，默认 `warning` |

调试建议：`python server.py --no-browser --log-level debug`；引擎的完整日志在 `logs/` 下最新那个文件里。退出用控制台 `Ctrl+C`，或者界面右上角 `?` →「退出程序」。

## 5. 测试

```powershell
# 规则与 HTTP 接口单元测试，不需要显卡和引擎
python -m unittest discover -s tests -v

# 服务起来之后，检查引擎状态
curl http://127.0.0.1:8765/api/status

# 服务起来之后，建一局、落子、等 AI 应手（需要可用的引擎和模型）
python tests/api_smoke.py
```

本机实测（Python 3.13 32 位）：`unittest discover` 共 81 个测试，2.7 秒跑完，OK（其中 3 个是预期失败，覆盖已知的非法落子/接口错误路径）。

## 6. 打包 Windows 便携版

### 6.1 安装 PyInstaller

```powershell
python -m pip install "pyinstaller>=6,<7"
```

注意：**用哪个位数的 Python 构建，就得到哪个位数的 exe**。发布版用的是 32 位 Python 3.13 打的包（32 位进程在 64 位 Windows 上能正常跑）；想打 64 位包就用 64 位 Python + 对应 PyInstaller。

### 6.2 打 exe

如果仓库里带了发版用的 `围棋陪练.spec`：

```powershell
python -m PyInstaller 围棋陪练.spec --noconfirm --clean
```

没有 spec 时用等价命令（默认 onedir 模式）：

```powershell
python -m PyInstaller --noconfirm --clean --name 围棋陪练 --noconsole --icon assets\app.ico server.py
```

产物是 `dist\围棋陪练\围棋陪练.exe` 和 `dist\围棋陪练\_internal\`。

### 6.3 组装便携目录

`web/`、`configs/`、`engine/`、`models/` 这些运行时文件不走 `--add-data`，打包后直接复制到 exe 同目录（PyInstaller 6 的 `--add-data` 默认会进 `_internal\`，而程序是按 exe 同目录找这些文件夹的）：

```powershell
$out = "dist\围棋陪练"
Copy-Item -Recurse -Force web, configs, engine, models $out
Copy-Item -Force README.md, THIRD_PARTY_NOTICES.md, 启动围棋陪练.bat, 停止围棋陪练.bat, 调试启动.bat $out
New-Item -ItemType Directory -Force -Path "$out\data\games" | Out-Null
```

打出来应该是：

```text
dist\围棋陪练\
├─ 围棋陪练.exe
├─ _internal\            # Python 运行时，必须跟 exe 一起分发
├─ web\ configs\ engine\ models\
├─ data\games\  logs\
├─ 启动围棋陪练.bat 停止围棋陪练.bat 调试启动.bat
└─ README.md THIRD_PARTY_NOTICES.md
```

体积参考（本机实测）：带引擎和两个模型的目录 225,033,001 字节（约 225 MB），压缩成 zip 约 207 MB；其中模型 184.4 MiB、引擎 15.0 MiB，去掉这两块约 15.3 MiB。所以想控制体积，可以不内置 `engine/` 和 `models/`，改成首次启动时按第 3 节手动放进去——但当前版本没有内置下载器，包不完整就是跑不起来，别把半成品发给别人。

### 6.4 冒烟验证打包结果

```powershell
# 双击启动 bat，或者直接跑 exe 并指定端口
Start-Process "dist\围棋陪练\围棋陪练.exe" -ArgumentList "--no-browser","--port","8799"
# 等 20 秒左右
curl http://127.0.0.1:8799/api/status
# 确认后退出
curl -X POST http://127.0.0.1:8799/api/quit
```

exe 是窗口模式（`--noconsole`），没有控制台输出，所以定位启动问题优先用源码模式跑。未签名的 exe 在别的机器上第一次运行可能触发 SmartScreen 提示，属正常现象。

发版流程（版本号、CHANGELOG、Release assets、校验和）见 `docs/RELEASE.md`。

## 7. 常见问题

**引擎异常 / 界面左下角显示「引擎异常」**
按顺序查：`engine\katago-opencl\katago.exe` 是否存在 → 模型文件是否完整（`-VerifyOnly`）→ 显卡驱动是否支持 OpenCL → 显存是否被别的程序占满 → 杀毒软件是否拦了 `katago.exe`。用 `--log-level debug` 起服务，看 `logs/` 里最新的 KataGo 日志。界面上点引擎卡片可以重启引擎。

**第一次启动很慢**
KataGo 在第一次使用某个模型 × 显卡组合时做 OpenCL 调优，缓存写到 `engine\katago-opencl\KataGoData\opencltuning\`。调优期间进程一直在跑，不是卡死；之后启动通常十几秒。

**端口被占用**
服务会自动尝试 8766–8784，控制台打印实际地址；也可以 `--port` 指定别的端口。

**打包后页面 404 或白屏**
99% 是 `web/` 没复制到 exe 同目录，或者把 `_internal\` 单独移走了。`_internal\` 必须和 exe 放在一起；`web/`、`configs/`、`engine/`、`models/` 必须在 exe 同目录（不是 `_internal\` 里面）。

**模型校验失败**
`-VerifyOnly` 会打印期望/实际的 SHA256 和字节数。确认下载的是原文件后，用 `-Force` 重新下载；脚本会把旧文件改名成 `*.bad` 保留，不会静默覆盖。

**`data/`、`logs/` 写不进去**
放在 `Program Files` 这类受保护目录会只读，服务会警告并降级（自动保存不可用）。解压到用户目录或 D 盘普通目录即可。

**Python 版本**
源码需要 3.10+；本项目开发和发布构建用的是 Python 3.13。没有第三方运行依赖，所以不需要 `requirements.txt`。

## 8. 相关文档

- [README.md](../README.md) / [README.en.md](../README.en.md)：功能与使用说明
- [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md)：KataGo 引擎与模型的来源、版本、许可说明
- [LICENSE](../LICENSE)：本项目代码许可
- [docs/RELEASE.md](RELEASE.md)：发版清单
- `configs/` 下的 `Analysis_Engine.md`、`GTP_Extensions.md`、`analysis_example.cfg` 是 KataGo 官方文档/示例的拷贝，版权归 KataGo 项目。
