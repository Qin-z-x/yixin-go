# 弈心 · 围棋陪练

**开箱即用的本地围棋陪练：打开浏览器就能和 KataGo 对坐，提示、形势、复盘全都实时算出来。**

不是脚本棋谱，不是云服务，不用注册账号。引擎、神经网络、棋谱全部留在你自己电脑上。

[![License: MIT](https://img.shields.io/badge/License-MIT-3b82f6?style=flat-square)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-0078d4?style=flat-square)](#运行要求)
[![Engine](https://img.shields.io/badge/engine-KataGo%201.18.1-16a34a?style=flat-square)](https://github.com/lightvector/KataGo)
[![Stars](https://img.shields.io/github/stars/Qin-z-x/yixin-go?style=flat-square&color=e0b44c)](https://github.com/Qin-z-x/yixin-go/stargazers)

<p align="center">
  <img src="docs/screenshots/01-home.png" width="49%" alt="中文界面">
  <img src="docs/screenshots/02-english.png" width="49%" alt="English UI">
</p>

<sub>实机截图：19 路 / 职业二段默认档（b10c512，每手 400 次搜索），引擎在本地 RTX 5060 Laptop 上已就绪。右上角可切中英文，也可以用 <code>?lang=en</code> 直接进英文界面。</sub>

---

## 这是什么

弈心是一个 **Windows 本地围棋陪练工具**：后端是纯 Python 标准库的小服务，前端是一块自己画的 Canvas 棋盘，中间连着你本机的 KataGo 分析引擎。

它对标的是「想认真下棋，但不想联网、不想被 AI 平台抽成、不想装一堆依赖」的那批人：

- 想练棋，但免费 AI 平台有次数限制、收费 AI 要订阅；
- 想复盘，但把棋谱传上去总觉得别扭；
- 想要一个能调强度、能看候选点、能随时悔棋的陪练，而不是一个冷冰冰的引擎命令行。

打开一次，之后全程离线可用。

## 60 秒开始

1. 到 [Releases](https://github.com/Qin-z-x/yixin-go/releases/latest) 下载 `Yixin-Go-Sparring-*-portable.zip`，解压到任意目录（**不要**放在中文路径或受保护的 `Program Files` 下）。
2. 双击 `启动围棋陪练.bat`。
3. 等 10–30 秒，引擎完成 GPU 预热，Edge 会以应用窗口打开棋盘。

默认执黑、19 路、中国规则。落子后 AI 自动应手。

> 第一次运行某个模型 × 显卡组合时，KataGo 会做一次 OpenCL 调优，可能需要几分钟；之后启动通常十几秒。
> 退出：右上角 `?` → 「退出程序」，或双击 `停止围棋陪练.bat`。

**从源码跑**：需要 Python 3.10+ 和本机 KataGo，详见 [docs/BUILD.md](docs/BUILD.md)。

## 陪练强度

| 档位 | 引擎配置 | 适合谁 |
| --- | --- | --- |
| 15级 / 10级 / 5级 / 1级 | b18 Human SL | 入门到进阶，按人类落子分布行棋，不会突然下出 AI 怪招 |
| 业余1段 / 业余3段 | b18 Human SL | 有段位基础，想练中盘战斗与官子 |
| **职业2段（默认）** | b10c512，400 visits | 想被压着打的日常陪练档 |
| 职业5段 | b10c512，800 visits | 复杂战斗、死活攻防 |
| 顶尖职业 | b10c512，1600 visits | 接近 KataGo 实战强度，用来找差距 |

几点说明，免得误会：

- 这些是 **引擎配置档位的名字**，不是通行证。档位由「模型 + 每手搜索量（visits）」决定，不是预置棋谱——每一手都是实时 MCTS 搜出来的。
- 档位参数取自 KataGo 官方对搜索量与棋力关系的经验值。如果你需要一个**正式**的棋力评级，请用 KataGo 官方 rating 工具，或参加受认证的评测；本软件的档位名不能当段位证书用。
- 每手耗时取决于显卡。作者本机 RTX 5060 Laptop + OpenCL 实测：400 visits 约 2–4 秒/手，800 visits 约 4–7 秒，1600 visits 约 8–15 秒。核显会明显更慢。

## 功能

- **对弈**：19 / 13 / 9 路，中国 / 日本规则，执黑执白，让 2–9 子，贴目可调。
- **AI 提示**：按 `H`，右侧给出 A/B/C 三个候选点的胜率、目差和变化图——可以直接用来对答案。
- **形势判断**：一键显示 KataGo 的地盘归属和目差预测，点第二次关闭。
- **死活数子**：双方停一手后自动标记死子，点棋子可手动增删，确认即终局。
- **AI 复盘**：终局后逐手分析：胜率损失曲线、好棋 / 失误标记、AI 首选与变化图，点曲线或棋谱任意一手都能跳回去看。
- **棋谱管理**：随时导出标准 SGF；结束的对局自动存到 `data/games/`。
- **纯本地**：不发任何请求到云端，断网也能下。

## 和别的 AI 围棋工具比

| | 弈心 | 在线 AI 平台 | Sabaki / Lizzie 一类 |
| --- | --- | --- | --- |
| 运行方式 | 本地 exe + 浏览器界面 | 网页 | 本地桌面程序 |
| 内置可直接对弈的 AI | ✅ 开箱即用 | ✅ 但受限/收费 | ⚠️ 需自己配引擎 |
| AI 提示 / 形势 / 复盘 | ✅ 内置同一引擎 | 部分 | ✅ 但要自己调 |
| 人类风格强度档 | ✅ Human SL 6 档 | 少见 | 少见 |
| 需要注册 / 联网 | ❌ | ✅ | ❌ |
| 源码可改 | ✅ MIT | ❌ | 多为 MIT/GPL |

Sabaki、Lizzie 都是很成熟的棋盘与分析师工具，功能比弈心全面得多——弈心的取舍是**把"能直接下、能直接复盘"这条最短路径做到零配置**，不追求做成通用 SGF 编辑器。

## 快捷键

| 按键 | 功能 |
| --- | --- |
| `H` | AI 提示（A/B/C 候选点） |
| `U` | 悔棋 |
| `P` | 停一手 |
| `Esc` | 关闭弹窗 |

## 技术细节

- **后端**：纯 Python 标准库 `ThreadingHTTPServer`，零 Web 框架、零第三方依赖。
- **引擎**：KataGo `analysis` JSON 协议，OpenCL 后端；同一进程同时加载主模型与 Human SL 模型，按档位切换。
- **前端**：原生 Canvas 绘制棋盘，高 DPI 自适应，木纹与棋子程序生成，无外部图片、无 CDN、无 Web 字体。
- **并发**：对弈、预分析、复盘、数子分属独立线程，引擎内部有请求队列与优先级，复盘不会卡住当前对局。
- **规则**：中国规则面积计分，日本规则地域 + 提子；让子棋白棋不额外加让子补偿。

## 运行要求

- Windows 10 / 11（x64）。
- 一块支持 OpenCL 的显卡（NVIDIA / AMD / Intel 核显都可以，NVIDIA 独显体验最好）。
- 约 250 MB 磁盘（含引擎与两个神经网络模型）。

## 常见问题

**首次启动很慢？** KataGo 在第一次用某个模型时会做 OpenCL 调优，缓存到 `engine/katago-opencl/KataGoData/opencltuning/`，之后启动十几秒。

**提示「引擎异常」？** 用 `调试启动.bat` 看控制台。常见原因：显存被占满、杀软拦截 `katago.exe`、模型文件不完整。关掉占显存的程序，点左下角引擎卡片可重启引擎。

**浏览器没自动打开？** 手动访问 `http://127.0.0.1:8765`。端口被占用时会自动尝试 8766–8784，控制台会打印实际地址。

**棋谱在哪？** `data/games/`，同时存 JSON（含 SGF 文本）和可下载的 SGF。

**能离线吗？** 能。模型和引擎都在本地，对局、提示、复盘都不需要网络。

## 许可证与致谢

本项目代码以 **MIT** 许可发布，见 [LICENSE](LICENSE)。

随包分发的 **KataGo 引擎同样是 MIT 许可**：其仓库根目录的 LICENSE 覆盖本体代码，`cpp/external/` 下的若干第三方库（CLBlast、cutlass、onnx 等）各自保留原有许可，详见上游仓库。两个神经网络模型由 KataGo 项目通过官方 Release 页面分发，本项目原样打包、不做修改。再分发便携版时请保留 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 以及 KataGo 的 LICENSE 文本（本仓库 `third_party/KataGo-LICENSE.txt` 有一份副本）。

- [KataGo](https://github.com/lightvector/KataGo) — David J. Wu 与贡献者，MIT。没有它这个项目不存在。
- [Sabaki](https://github.com/SabakiHQ/Sabaki)、[Lizzie](https://github.com/featurecat/lizzie) — 棋盘与 AI 复盘交互上的前辈，很多设计思路来自它们。
- Python、PyInstaller 各自遵循其许可证。

## 参与

- 想加功能？先看 [ROADMAP.md](ROADMAP.md) 里"明确不做"的部分，避免白写。
- 提 Issue 前先看一遍 [CONTRIBUTING.md](CONTRIBUTING.md)。
- 交流请遵守 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)——只有一条要求：把话说清楚，对人客气。
- 改了界面、修了 bug、补了文档都欢迎 PR。

---

<sub>如果你也在找一个能安安静静陪你下棋、不下完就把棋谱传上云的东西，这个项目就是为你写的。</sub>
