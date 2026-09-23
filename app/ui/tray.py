"""系统托盘图标 + 右键菜单。"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .. import config as cfg
from ..theme import make_icon


class Tray(QSystemTrayIcon):
    """托盘：双击打开窗口；右键菜单能快速新建、暂停提醒、切换主题、退出。"""

    open_requested = Signal()
    new_requested = Signal()
    refresh_requested = Signal()
    quit_requested = Signal()
    toggle_pause = Signal(bool)          # True=暂停提醒

    def __init__(self, theme, settings, scheduler, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.settings = settings
        self.scheduler = scheduler
        self._paused = False

        self.setIcon(make_icon(128, theme.c))
        self.setToolTip(cfg.APP_NAME)
        self.activated.connect(self._on_activated)

        self._build_menu()
        self.update_tooltip()

    # ------------------------------------------------------------------
    def _build_menu(self):
        m = QMenu()

        a_open = QAction("打开主窗口", m)
        a_open.triggered.connect(self.open_requested)
        m.addAction(a_open)

        a_new = QAction("新建提醒…", m)
        a_new.triggered.connect(self.new_requested)
        m.addAction(a_new)

        a_refresh = QAction("刷新", m)
        a_refresh.triggered.connect(self.refresh_requested)
        m.addAction(a_refresh)

        m.addSeparator()

        self.a_pause = QAction("暂停提醒（1 小时）", m)
        self.a_pause.setCheckable(True)
        self.a_pause.triggered.connect(self._toggle_pause)
        m.addAction(self.a_pause)

        a_today = QAction("查看今天", m)
        a_today.triggered.connect(self.open_requested)
        m.addAction(a_today)

        m.addSeparator()

        a_quit = QAction("退出", m)
        a_quit.triggered.connect(self.quit_requested)
        m.addAction(a_quit)

        self.setContextMenu(m)
        self._menu = m

    # ------------------------------------------------------------------
    def _on_activated(self, reason):
        if reason in (QSystemTrayIcon.DoubleClick, QSystemTrayIcon.Trigger):
            self.open_requested.emit()

    def _toggle_pause(self):
        self._paused = self.a_pause.isChecked()
        self.scheduler.set_enabled(not self._paused)
        self.a_pause.setText("恢复提醒" if self._paused else "暂停提醒（1 小时）")
        self.update_tooltip()

    # ------------------------------------------------------------------
    def update_tooltip(self, summary: str = ""):
        from .. import stats as stats_mod
        try:
            st = stats_mod.day_stats(self.scheduler.db, date.today())
            tip = f"{cfg.APP_NAME}\n今天 {st['done']}/{st['total']} 已完成"
            if st["left"]:
                tip += f"，还剩 {st['left']} 件"
        except Exception as e:
            # 只是托盘的悬浮提示，失败不影响提醒功能，但留个记录方便排查
            cfg.log_problem("托盘提示更新失败", e)
            tip = cfg.APP_NAME
        if self._paused:
            tip += "\n（提醒已暂停）"
        self.setToolTip(tip)
