# Third-Party Notices

本项目代码为 MIT（见根目录 `LICENSE`）。便携版另外打包了下列第三方组件。

## KataGo

本应用通过 KataGo 的 `analysis` JSON 协议调用其分析引擎，引擎以独立进程运行。

- 项目：https://github.com/lightvector/KataGo
- 版本：v1.18.1（Windows OpenCL 构建）
- 许可证：**MIT**（Copyright 2025 David J Wu "lightvector" and/or other authors）
- 需要注意：KataGo 仓库 `cpp/external/` 下的第三方库（CLBlast、composable_kernel_fmha、
  cudnn-frontend、cutlass、filesystem、half、httplib、katagocoreml、mozilla-cacerts、
  nlohmann_json、sgfmill、onnx、tclap 等）各自保留其原有许可证，`cpp/core/sha2.cpp`
  亦内嵌了自己的许可证声明。若你修改并再分发 KataGo 本体，请按上游 `LICENSE` 逐项处理。
- 上游许可证全文副本：`third_party/KataGo-LICENSE.txt`

## 神经网络模型

下列模型通过 KataGo 官方 Release 页面分发，本项目原样打包、未做任何修改：

- `b10c512h8nbt3tflrs-fson-silu-rsnh.bin.gz`（KataGo v1.17.1 发布，transformer 主网络）
- `b18c384nbt-humanv0.bin.gz`（KataGo v1.15.0 发布，Human SL 网络）

模型的授权与使用条款以上游发布页面和 KataGo 仓库说明为准；本仓库不对模型做任何
权利主张。分发时需要同时保留本节说明与 KataGo 的版权声明。

## 文档片段

`configs/Analysis_Engine.md`、`configs/GTP_Extensions.md`、`configs/analysis_example.cfg`
来自 KataGo 官方仓库（MIT，同上），保留原内容以便对照，未做改动。

## Python / PyInstaller

便携版由 Python 与 PyInstaller 构建。Python 使用 PSF License；PyInstaller 使用
GPLv2 + bootloader 例外（该例外允许以非 GPL 许可分发打包产物）。项目自身代码仍为 MIT。
