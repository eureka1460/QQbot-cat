@echo off
chcp 65001
title QQBot-Cat 一键启动器

echo ==========================================
echo    正在启动 QQbot-cat 自动化环境
echo ==========================================

:: 1. 启动 NapCat 后端
echo [1/2] 正在启动 NapCat 后端...
start "NapCat-Backend" /D "D:\NapCat Shell Windows OneKey\NapCat.44498.Shell" cmd /c "napcat.quick.bat"

:: 等待 5 秒，给 NapCat 一点启动时间，防止 Python 连不上
timeout /t 5 /nobreak > nul

:: 2. 启动 Python 机器人大脑
echo [2/2] 正在启动 Python 机器人大脑...
:: 自动运行 bot.py
start "Bot-Brain" /D "D:\QQbot-cat" cmd /k "python bot.py"

echo ==========================================
echo    启动指令已发送！
echo    请检查弹出的两个黑窗口是否正常运行。
echo ==========================================
pause