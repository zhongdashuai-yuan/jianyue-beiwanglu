@echo off
chcp 65001 >nul
REM ============================================================
REM  一键打包 exe  /  build the exe
REM
REM  两种模式：
REM    双击本文件                          -> 文件夹版 dist\备忘录提醒\
REM    set MEMO_ONEFILE=1 && build_exe.bat -> 单文件版 dist_onefile\
REM
REM  做三件事：
REM    1. 生成图标（如果还没有）
REM    2. PyInstaller 打包
REM    3. 建桌面 + 开始菜单快捷方式
REM
REM  单文件版必须输出到 dist_onefile\，不能和文件夹版共用 dist\。
REM  两个都往 dist\ 写的话，后打包的会覆盖先打包的 _internal，
REM  结果就是「备忘录提醒.exe（旧）+ _internal（新）」，
REM  双击弹一个全白窗口。这个坑踩过一次，所以这里按模式分开目录。
REM
REM  ⚠️ 注意：下面 set 语句里故意只用 ASCII。
REM     cmd.exe 是按系统 OEM 代码页（中文机器上是 GBK）解析 .bat 文件的，
REM     文件里那一行 chcp 65001 救不了 set —— 等它执行时 set 早就解析完了。
REM     中文变量值会被解析成乱码，进而写出乱码文件名。中文只放在 echo 里。
REM ============================================================

setlocal
cd /d "%~dp0"

if defined MEMO_ONEFILE (
    set "DISTARG=--distpath dist_onefile"
    set "MODENAME=onefile"
) else (
    set "DISTARG="
    set "MODENAME=folder"
)

echo.
if "%MODENAME%"=="onefile" (echo 打包模式: 单文件版) else (echo 打包模式: 文件夹版)
echo.

echo [1/4] 检查打包依赖...
python -c "import PyInstaller" 2>nul
if errorlevel 1 (
    echo     正在安装 PyInstaller...
    python -m pip install pyinstaller
    if errorlevel 1 goto :fail
)

echo.
echo [2/4] 生成应用图标...
python -c "import sys; sys.path.insert(0,'.'); from PySide6.QtWidgets import QApplication; app=QApplication([]); from app.theme import save_ico; from app import config as cfg; print('     图标:', save_ico(cfg.ICON_PATH))"
if errorlevel 1 (
    echo     图标生成失败，但可以继续打包（会用默认图标）
)

echo.
echo [3/4] 开始打包（第一次会比较慢，约 1-3 分钟）...
python -m PyInstaller memo.spec --noconfirm --clean %DISTARG%
if errorlevel 1 goto :fail

echo.
echo [4/4] 创建快捷方式...
python -c "import sys; sys.path.insert(0,'.'); import os; os.environ.setdefault('QT_QPA_PLATFORM','offscreen'); from app.autostart import install_shortcuts; print('     结果:', install_shortcuts())"

echo.
echo ============================================================
echo  打包完成！
echo.
echo  程序位置:
if "%MODENAME%"=="onefile" (
    echo    %cd%\dist_onefile\备忘录提醒单文件.exe
) else (
    echo    %cd%\dist\备忘录提醒\备忘录提醒.exe
)
echo  数据位置: E:\Memo\data\   （重新打包不会丢数据）
echo.
echo  部署到正式目录时，两种模式都要更新一次：
echo    copy dist\备忘录提醒\*  E:\Memo\备忘录提醒\
echo    copy dist_onefile\*.exe E:\Memo\备忘录提醒\
echo.
echo  只更新其中一个，就可能出现「新代码配旧依赖」的白窗口问题。
echo ============================================================
echo.
pause
exit /b 0

:fail
echo.
echo *** 打包失败，请把上面的错误信息发给我 ***
echo.
pause
exit /b 1
