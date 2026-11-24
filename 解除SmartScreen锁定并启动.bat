@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 解除 SmartScreen 锁定并启动

echo 正在解除此文件夹内文件的“网络下载”锁定……
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -LiteralPath '%~dp0' -Recurse -File -ErrorAction SilentlyContinue | Unblock-File -ErrorAction SilentlyContinue" >nul 2>&1
echo 完成。
echo.

if exist "围棋陪练.exe" (
  echo 正在启动弈心围棋陪练……
  start "" "围棋陪练.exe"
  exit /b 0
)

echo 未找到“围棋陪练.exe”。
echo 请把本文件放到“围棋陪练.exe”所在的文件夹，再双击运行。
pause
