"""程序入口。

在 PyCharm 里直接右键这个文件 -> Run 'main' 就能跑。
打包成 exe 用的也是这个文件（见 build_exe.bat / memo.spec）。

启动流程：
    单实例检查 -> 建数据目录 -> 开数据库 -> 生成主题/图标 -> 建主窗口
    -> 建托盘 -> 启动提醒调度器 -> 进事件循环
"""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

# 让 `python main.py` 在任意工作目录下都能 import app 包
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import Qt, QTimer                              # noqa: E402
from PySide6.QtGui import QIcon, QPixmap                           # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox            # noqa: E402

from app import config as cfg                                      # noqa: E402
from app.autostart import SingleInstance, is_autostart_enabled, set_autostart  # noqa: E402
from app.database import Database                                  # noqa: E402
from app.reminder import ReminderScheduler                         # noqa: E402
from app.theme import ThemeManager, make_icon, save_ico            # noqa: E402
from app.ui.main_window import MainWindow                          # noqa: E402
from app.ui.popup import PopupManager                              # noqa: E402
from app.ui.tray import Tray                                       # noqa: E402

AUTOSTART_FLAG = cfg.AUTOSTART_ARGS


# ---------------------------------------------------------------------------
# 全局异常兜底：写日志 + 弹窗提示，避免"窗口一闪就没了"
# ---------------------------------------------------------------------------

def _install_excepthook():
    def hook(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        try:
            cfg.DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(cfg.LOG_PATH, "a", encoding="utf-8") as f:
                from datetime import datetime
                f.write(f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S} =====\n{text}")
        except Exception:
            pass
        try:
            sys.__stderr__.write(text)
        except Exception:
            pass
        try:
            if QApplication.instance() is not None:
                QMessageBox.critical(
                    None, f"{cfg.APP_NAME} 出错了",
                    f"程序遇到错误，已记录到：\n{cfg.LOG_PATH}\n\n"
                    f"{exc_type.__name__}: {exc}")
        except Exception:
            pass

    sys.excepthook = hook


def main() -> int:
    _install_excepthook()

    # 高分屏：Qt6 默认已开启缩放，这里只设置取整策略，避免字体发虚
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    autostart_mode = AUTOSTART_FLAG in sys.argv
    app = QApplication(sys.argv)
    app.setApplicationName(cfg.APP_NAME)
    app.setApplicationDisplayName(cfg.APP_NAME)
    app.setOrganizationName(cfg.ORG_NAME)
    app.setQuitOnLastWindowClosed(False)      # 关窗口不退出（托盘常驻）

    # ---- 单实例 ----
    guard = SingleInstance()
    if not guard.acquire():
        guard.bring_existing_to_front()
        return 0

    # ---- 数据目录 ----
    cfg.DATA_DIR.mkdir(parents=True, exist_ok=True)
    cfg.BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    # ---- 数据库 ----
    db = Database(cfg.DB_PATH)

    # ---- 设置 ----
    settings = cfg.Settings(cfg.SETTINGS_PATH)
    first_run = not settings.get("_first_run_done", False)
    if first_run:
        # 首次运行：按确认过的默认值开启开机自启（否则早上的提醒会错过）
        if settings.get("autostart", True) and not is_autostart_enabled():
            set_autostart(True)
        settings.set("_first_run_done", True)

    # ---- 主题 ----
    theme = ThemeManager(app, settings)
    theme.apply()

    # ---- 图标（首次生成一个 png 备打包用）----
    if not cfg.ICON_PATH.exists():
        save_ico(cfg.ICON_PATH)

    # ---- 主窗口 + 调度器 ----
    scheduler = ReminderScheduler(db, settings)
    win = MainWindow(db, scheduler, theme, settings)

    # ---- 提醒弹窗管理 ----
    def on_popup_done(task_id: int):
        t = db.get_task(task_id)
        if t:
            planned = scheduler.planned_date_of(t)
            scheduler.complete(t, planned=planned)
        win.refresh_all()

    def on_popup_snooze(task_id: int, minutes: int):
        t = db.get_task(task_id)
        if t:
            scheduler.snooze(t, minutes)
        win.refresh_all()

    def on_popup_skip(task_id: int):
        t = db.get_task(task_id)
        if t:
            scheduler.skip_once(t)
        win.refresh_all()

    popups = PopupManager(theme, settings, on_popup_done, on_popup_snooze, on_popup_skip)
    win._popups = popups

    # ---- 托盘 ----
    tray = Tray(theme, settings, scheduler)
    app._memo_tray = tray          # 供 main_window 发系统通知用
    tray.open_requested.connect(lambda: _show_window(win))
    tray.new_requested.connect(lambda: (_show_window(win), win.new_task()))
    tray.refresh_requested.connect(win.refresh_all)
    tray.quit_requested.connect(lambda: _quit(win, popups, tray, db, guard))
    tray.show()

    # ---- 打包成 exe 后，第一次运行自动建桌面/开始菜单快捷方式 ----
    if getattr(sys, "frozen", False) and first_run:
        try:
            from app.autostart import install_shortcuts
            res = install_shortcuts()
            win.settings.set("_shortcuts_created", bool(res.get("ok")))
        except Exception:
            pass

    # ---- 调度器 ----
    scheduler.due.connect(win.on_due)
    scheduler.start(catch_up=True)

    # ---- 开机自启时静默：不弹主窗口 ----
    if not autostart_mode:
        _show_window(win)

    # ---- 退出时收尾 ----
    app.aboutToQuit.connect(lambda: _cleanup(popups, tray, scheduler, db, guard))

    return app.exec()


def _show_window(win: MainWindow):
    win.show()
    win.setWindowState(win.windowState() & ~Qt.WindowMinimized)
    win.raise_()
    win.activateWindow()


def _quit(win: MainWindow, popups, tray, db, guard):
    popups.close_all()
    try:
        win.force_quit()
    except Exception:
        pass
    tray.hide()
    QApplication.quit()


def _cleanup(popups, tray, scheduler, db, guard):
    try:
        popups.close_all()
    except Exception:
        pass
    try:
        scheduler.stop()
    except Exception:
        pass
    try:
        tray.hide()
    except Exception:
        pass
    try:
        db.backup()
        db.close()
    except Exception:
        pass
    try:
        guard.release()
    except Exception:
        pass


if __name__ == "__main__":
    sys.exit(main())
