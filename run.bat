@echo off
chcp 65001 >nul
REM ============================================================
REM  直接运行（不需要打包）
REM
REM  用 pythonw.exe 启动，不会留黑窗口。
REM  如果启动后没反应，改用下面被注释掉的 show 模式看报错。
REM ============================================================

cd /d "%~dp0"

REM --- 静默启动（推荐，平时用这个）---
start "" pythonw.exe "%~dp0main.py"

REM --- 调试模式：保留控制台，能看到报错 ---
REM python.exe "%~dp0main.py"
REM pause
