@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 弈心 · 调试启动
echo 正在启动弈心围棋陪练（调试模式）...
echo 关闭此窗口即可停止程序。
echo.
python server.py
echo.
echo 程序已退出。按任意键关闭窗口。
pause >nul
