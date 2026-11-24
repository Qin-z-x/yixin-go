@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 弈心 · 围棋陪练

rem 清除从网络下载后附带的 Mark-of-the-Web，避免 SmartScreen 反复拦截
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -LiteralPath '%~dp0' -Recurse -File -ErrorAction SilentlyContinue | Unblock-File -ErrorAction SilentlyContinue" >nul 2>&1

if exist "围棋陪练.exe" (
  start "" "围棋陪练.exe"
  exit /b 0
)

where pythonw >nul 2>&1
if %errorlevel%==0 (
  start "" pythonw "server.py"
  exit /b 0
)

echo 未找到围棋陪练.exe，也未找到 pythonw，请先阅读 README.md。
pause
