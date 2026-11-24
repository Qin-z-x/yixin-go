@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 弈心 · 源码模式启动

rem 如果 Windows SmartScreen 拦截了围棋陪练.exe，可改用此模式。
rem 此模式直接调用本机 Python 运行 server.py，不经过 exe。

powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -LiteralPath '%~dp0' -Recurse -File -ErrorAction SilentlyContinue | Unblock-File -ErrorAction SilentlyContinue" >nul 2>&1

where pythonw >nul 2>&1
if errorlevel 1 (
  echo 未找到 pythonw。请安装 Python 3.10+ 并勾选 Add to PATH，
  echo 或直接双击围棋陪练.exe 并在 SmartScreen 中选择“运行”。
  pause
  exit /b 1
)

start "" pythonw "server.py"
exit /b 0
