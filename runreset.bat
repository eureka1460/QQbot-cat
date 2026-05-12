@echo off
chcp 65001 > nul
title QQBot-Cat 自动重启管理器

echo ==========================================
echo    QQBot-Cat 自动重启管理器
echo    每隔随机时间（20~55分钟）自动重启
echo ==========================================

:LOOP

echo.
echo [%date% %time%] ====== 正在启动程序 ======

:: 1. 启动 NapCat 后端
echo [1/2] 正在启动 NapCat 后端...
start "NapCat-Backend" /D "D:\NapCat Shell Windows OneKey\NapCat.44498.Shell" cmd /c "napcat.quick.bat"

:: 等待 5 秒，给 NapCat 一点启动时间
ping -n 6 127.0.0.1 > nul

:: 2. 启动 Python 机器人大脑
echo [2/2] 正在启动 Python 机器人大脑...
start "Bot-Brain" /D "D:\QQbot-cat" cmd /k "python bot.py"

:: 生成随机等待时间（单位：秒）
:: 范围：20分钟（1200秒）~ 55分钟（3300秒）
set /a "WAIT_TIME=%random% %% 2101 + 1200"
set /a "WAIT_MIN=%WAIT_TIME% / 60"

echo.
echo ==========================================
echo [%date% %time%] 程序已启动
echo 将在 %WAIT_TIME% 秒后（约 %WAIT_MIN% 分钟）重启
echo ==========================================

:: 等待指定时间（用 ping 实现计时）
ping -n %WAIT_TIME% 127.0.0.1 > nul

echo.
echo [%date% %time%] ====== 准备重启，正在关闭旧进程 ======

:: 关闭 NapCat 相关进程（通过窗口标题匹配）
echo 正在关闭 NapCat 进程...
taskkill /f /fi "WINDOWTITLE eq NapCat*" 2>nul

:: 关闭 Python 机器人进程
echo 正在关闭 Python 机器人进程...
taskkill /f /fi "WINDOWTITLE eq Bot-Brain*" 2>nul

:: 额外等待确保进程完全关闭
ping -n 6 127.0.0.1 > nul

echo [%date% %time%] 旧进程已关闭，准备重新启动...
echo.

:: 回到循环开始
goto LOOP
