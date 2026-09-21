@echo off
chcp 65001 >nul
REM ============================================================
REM  一键打包成文件夹版 exe（启动快，适合本机长期使用）
REM
REM  做三件事：
REM    1. 生成图标（如果还没有）
REM    2. PyInstaller 打包 -> dist\备忘录提醒\
REM    3. 建桌面 + 开始菜单快捷方式
REM
REM  用法：双击本文件，或在 PyCharm 的 Terminal 里执行 build_exe.bat
REM ============================================================

setlocal
cd /d "%~dp0"

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
python -m PyInstaller memo.spec --noconfirm --clean
if errorlevel 1 goto :fail

echo.
echo [4/4] 创建快捷方式...
python -c "import sys; sys.path.insert(0,'.'); import os; os.environ.setdefault('QT_QPA_PLATFORM','offscreen'); from app.autostart import install_shortcuts; print('     结果:', install_shortcuts())"

echo.
echo ============================================================
echo  打包完成！
echo.
echo  程序位置: %cd%\dist\备忘录提醒\备忘录提醒.exe
echo  数据位置: E:\Memo\data\   （重新打包不会丢数据）
echo.
echo  下一步（手动做一次，之后开机自启会自己生效）：
echo    打开程序 -^> 设置 -^> 勾选「开机自动启动」-^> 保存
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
