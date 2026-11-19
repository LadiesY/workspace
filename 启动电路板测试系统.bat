@echo off
chcp 65001 >nul
title 电路板测试系统
cd /d "%~dp0"

set "PYW=%~dp0runtime\python\pythonw.exe"
set "PY=%~dp0runtime\python\python.exe"
set "APP=%~dp0main.py"

if not exist "%APP%" goto NOAPP

set "RUN=%PYW%"
if not exist "%PYW%" set "RUN=%PY%"
if not exist "%RUN%" goto NOPY

rem pythonw 启动，不带黑色控制台窗口；出错信息由程序自己弹框
start "" "%RUN%" "%APP%"
exit /b 0

:NOAPP
echo.
echo [错误] 找不到主程序 main.py
echo        位置：%APP%
echo        请确认复制的是完整文件夹。
echo.
pause
exit /b 1

:NOPY
echo.
echo [错误] 找不到内置 Python 运行时
echo        位置：%PYW%
echo        这个文件夹可能没复制完整，请重新复制整个文件夹。
echo.
pause
exit /b 1
