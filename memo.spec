# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置。

用法：
    # 文件夹版（默认，启动 1~2 秒，推荐本机长期用）
    pyinstaller memo.spec --noconfirm

    # 单文件版（一个 exe 好拷贝，但每次启动要自解压，约 3~6 秒）
    set MEMO_ONEFILE=1 && pyinstaller memo.spec --noconfirm

产物：
    文件夹版  dist\\备忘录提醒\\备忘录提醒.exe
    单文件版  dist\\备忘录提醒单文件.exe

说明：
  * 数据不在这个文件夹里，而是存在 E:\\Memo\\data\\，所以重新打包不会丢数据。
"""

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_dynamic_libs

ROOT = Path(SPECPATH)          # noqa: F821  (PyInstaller 注入的变量)
APP_NAME = "备忘录提醒"
# 环境变量切换单文件 / 文件夹模式
ONEFILE = os.environ.get("MEMO_ONEFILE", "") not in ("", "0", "false")

# Qt 的 dll 需要显式收集，否则打包出来会报 "could not find Qt platform plugin"
binaries = collect_dynamic_libs("PySide6") + collect_dynamic_libs("shiboken6")

datas = [
    (str(ROOT / "assets"), "assets"),
]

# ★ 关键：PyInstaller 的 hook-PySide6.py 只认发行版名 "PySide6"，
#   而本机装的是 "PySide6-Essentials"（PyPI 上 Essentials 的发行名就是它），
#   导致 hook 里 `pyside6_library_info.collect_extra_binaries()` 那一步被跳过，
#   `shiboken6` 包完全没被收集，exe 一启动就报
#       ImportError: DLL load failed while importing QtCore
#   所以这里必须显式把 shiboken6 相关的东西自己收一遍。
hiddenimports = [
    "PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets",
    "shiboken6", "shiboken6.Shiboken",
    "PySide6.support.deprecated",
]

a = Analysis(                                  # noqa: F821
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # 明确排除用不到的大块头，能显著减小体积
    # ★ 注意：绝对不要排除 shiboken6 / shiboken6.Shiboken —— 它是 PySide6 的
    #   必需二进制模块，排掉之后 exe 启动会报 "No module named 'shiboken6.Shiboken'"。
    excludes=[
        "tkinter", "unittest", "pydoc", "doctest", "test",
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
        "PySide6.Qt3DCore", "PySide6.QtCharts", "PySide6.QtDataVisualization",
        "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtPdf",
        "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
        "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtPositioning",
        "PySide6.QtSerialPort", "PySide6.QtSql", "PySide6.QtTest",
        "PySide6.QtWebSockets", "PySide6.QtWebChannel", "PySide6.QtSvgWidgets",
        "numpy", "PIL",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)                              # noqa: F821

exe = EXE(                                     # noqa: F821
    pyz,
    a.scripts,
    # 单文件模式：把 binaries/datas 也塞进 exe；文件夹模式：交给下面的 COLLECT
    a.binaries if ONEFILE else [],
    a.datas if ONEFILE else [],
    [],
    exclude_binaries=not ONEFILE,
    name=APP_NAME + ("单文件" if ONEFILE else ""),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                # 不用 UPX：容易被杀毒软件误报
    console=False,            # 不显示黑窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "assets" / "app_icon.ico"),
    runtime_tmpdir=None if ONEFILE else None,   # 单文件默认解压到 %TEMP%
)

if ONEFILE:
    # 单文件模式不需要 COLLECT
    pass
else:
    coll = COLLECT(                            # noqa: F821
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name=APP_NAME,
    )
