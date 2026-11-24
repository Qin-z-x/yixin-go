@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在停止弈心围棋陪练...
taskkill /IM "围棋陪练.exe" /F >nul 2>&1
taskkill /IM "katago.exe" /F >nul 2>&1
echo 已停止。按任意键关闭。
pause >nul
