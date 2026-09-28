"""主窗口：左侧边栏（今日概览 + 导航 + 标签）+ 右侧内容区（列表/日历/统计）。

骨架长这样：
    MainWindow
      ├─ Sidebar        渐变卡片：环形进度、导航项、标签筛选
      └─ Content
           ├─ Header   日期 / 搜索 / 新提醒
           └─ QStackedWidget
                ├─ ListView
                ├─ CalendarView
                └─ StatsView
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from PySide6.QtCore import (QEasingCurve, QEvent, QPropertyAnimation, QSize, Qt,
                            QTimer, Signal)
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog,
                               QFileDialog, QFrame, QGraphicsOpacityEffect, QHBoxLayout,
                               QLabel, QLineEdit, QMenu, QMessageBox, QPushButton,
                               QScrollArea, QSizePolicy, QSpinBox, QStackedWidget,
                               QSystemTrayIcon, QTextEdit, QVBoxLayout, QWidget)

from .. import config as cfg
from .. import stats as stats_mod
from ..holidays import CALENDAR
from ..models import Task
from ..recurrence import compute_next_at, describe
from ..theme import make_icon
from . import dialogs
from .admin_view import AdminView
from .class_view import ClassView
from .views import CalendarView, ListView, StatsView
from .widgets import EmptyState, RingProgress, chip, soft_shadow


class Sidebar(QFrame):
    nav_changed = Signal(str)
    tag_filter_changed = Signal(list)
    settings_clicked = Signal()

    def __init__(self, db, theme, settings, parent=None):
        super().__init__(parent)
        self.db = db
        self.theme = theme
        self.settings = settings
        self.setObjectName("Panel")
        self.setFixedWidth(216)
        self._tags: list[str] = list(settings.get("tag_filter") or [])

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(10)

        # ---- 顶部 logo + 名字 ----
        head = QHBoxLayout()
        head.setSpacing(8)
        self.logo = QLabel()
        self.logo.setFixedSize(26, 26)
        self.logo.setPixmap(make_icon(64, theme.c).pixmap(26, 26))
        head.addWidget(self.logo)
        name = QLabel(cfg.APP_NAME)
        name.setStyleSheet("font-size: 15px; font-weight: 600;")
        head.addWidget(name)
        head.addStretch(1)
        lay.addLayout(head)

        # ---- 今日概览卡片 ----
        overview = QFrame()
        overview.setObjectName("Card")
        soft_shadow(overview, theme.c["shadow"], blur=12, dy=2, alpha=55)
        ol = QVBoxLayout(overview)
        ol.setContentsMargins(10, 10, 10, 10)
        ol.setSpacing(4)
        self.ring = RingProgress(size=94, thickness=8)
        self.ring.apply_theme(theme.c)
        ol.addWidget(self.ring, 0, Qt.AlignHCenter)

        self.lbl_today = QLabel("今天")
        self.lbl_today.setObjectName("Sub")
        self.lbl_today.setAlignment(Qt.AlignCenter)
        ol.addWidget(self.lbl_today)

        self.lbl_summary = QLabel("还没有安排")
        self.lbl_summary.setObjectName("Muted")
        self.lbl_summary.setAlignment(Qt.AlignCenter)
        self.lbl_summary.setWordWrap(True)
        ol.addWidget(self.lbl_summary)
        lay.addWidget(overview)

        # ---- 导航 ----
        self.nav_group = QButtonGroup(self)
        self.nav_buttons: dict[str, QPushButton] = {}
        for key, icon, label in (("list", "📋", "列表"), ("calendar", "📅", "日历"),
                                 ("stats", "📊", "统计"), ("class", "📣", "班级"),
                                 ("admin", "🛠️", "管理")):
            b = QPushButton(f"  {icon}   {label}")
            b.setObjectName("NavItem")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self._on_nav(k))
            self.nav_group.addButton(b)
            lay.addWidget(b)
            self.nav_buttons[key] = b
        # 「管理」的显隐由 _update_admin_nav() 统一决定（本函数末尾会调一次）。
        # 这里不要另外写一份隐藏逻辑 —— 两套机制会互相兜住，
        # 导致"改坏了其中一个，测试还是绿的"。

        # ---- 标签筛选 ----
        self.lbl_tags = QLabel("标签筛选")
        self.lbl_tags.setObjectName("Muted")
        lay.addWidget(self.lbl_tags)

        tag_scroll = QScrollArea()
        tag_scroll.setWidgetResizable(True)
        tag_scroll.setFrameShape(QFrame.NoFrame)
        tag_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.tag_holder = QWidget()
        self.tag_box = QVBoxLayout(self.tag_holder)
        self.tag_box.setContentsMargins(0, 0, 0, 0)
        self.tag_box.setSpacing(4)
        self.tag_box.addStretch(1)
        tag_scroll.setWidget(self.tag_holder)
        lay.addWidget(tag_scroll, 1)

        # ---- 底部设置 ----
        btn_set = QPushButton("  ⚙️   设置")
        btn_set.setObjectName("NavItem")
        btn_set.setCursor(Qt.PointingHandCursor)
        btn_set.clicked.connect(self.settings_clicked)
        lay.addWidget(btn_set)

        self.refresh_tags()
        self.set_active("list")

    # ------------------------------------------------------------------
    def _on_nav(self, key: str):
        self.set_active(key)
        self.nav_changed.emit(key)

    def set_active(self, key: str):
        for k, b in self.nav_buttons.items():
            b.setChecked(k == key)

    def refresh_tags(self):
        while self.tag_box.count() > 1:
            item = self.tag_box.takeAt(0)
            w = item.widget()
            if w:
                # 先隐藏再断开父关系：setParent(None) 会把控件变成顶层窗口，
                # 若它当时可见，就会在销毁前以独立小窗口闪一下（用户报过这个现象）。
                w.hide()
                w.setParent(None)
                w.deleteLater()
        allb = chip("全部标签", not self._tags)
        allb.clicked.connect(lambda: self._toggle_tag("__all__"))
        self.tag_box.insertWidget(0, allb)
        for tag in self.db.tags():
            b = chip(tag.name, tag.name in self._tags, tag.color)
            b.clicked.connect(lambda _=False, n=tag.name: self._toggle_tag(n))
            self.tag_box.insertWidget(self.tag_box.count() - 1, b)

    def _toggle_tag(self, name: str):
        # 整个处理过程包一层：万一出错要写进日志（含完整堆栈），
        # 否则只会弹一个错误框、日志里什么都查不到，很难定位。
        try:
            if name == "__all__":
                self._tags = []
            elif name in self._tags:
                self._tags.remove(name)
            else:
                self._tags.append(name)
            self.settings.set("tag_filter", self._tags)
            self.refresh_tags()
            self.tag_filter_changed.emit(list(self._tags))
        except Exception as e:
            cfg.log_problem("切换标签筛选失败", e,
                            f"标签={name!r} 当前筛选={self._tags!r}")
            raise

    def refresh_overview(self):
        st = stats_mod.day_stats(self.db, date.today())
        self.ring.set_has_tasks(st["total"] > 0)
        self.ring.set_ratio(st["rate"])
        self.ring.set_label(f"{st['done']}/{st['total']}" if st["total"] else "没有安排")
        wd = cfg.WEEKDAY_CN[date.today().isoweekday()]
        holiday = CALENDAR.label(date.today())
        txt = f"{date.today().month}月{date.today().day}日 {wd}"
        if holiday:
            txt += f"（{holiday}）"
        self.lbl_today.setText(txt)
        if st["total"] == 0:
            self.lbl_summary.setText("今天没有安排，休息一下")
        elif st["left"] == 0:
            self.lbl_summary.setText("今天全部完成 🎉")
        else:
            bits = [f"还剩 {st['left']} 件"]
            if st["overdue"]:
                bits.append(f"逾期 {st['overdue']} 件")
            self.lbl_summary.setText(" · ".join(bits))

    def apply_theme(self, c: dict):
        self.ring.apply_theme(c)
        self.logo.setPixmap(make_icon(64, c).pixmap(26, 26))


class MainWindow(QWidget):
    """主窗口。关闭时默认最小化到托盘。"""

    quit_requested = Signal()

    def __init__(self, db, scheduler, theme, settings, parent=None, class_sync=None):
        super().__init__(parent)
        self.db = db
        self.scheduler = scheduler
        self.theme = theme
        self.settings = settings
        self.class_sync = class_sync     # 班级同步对象；None 表示不启用班级页
        self._force_quit = False
        self._popups = None          # 由 main.py 注入的 PopupManager

        self.setObjectName(cfg.APP_NAME)
        self.setWindowTitle(cfg.APP_NAME)
        self.setWindowIcon(make_icon(128, theme.c))
        self.resize(1060, 700)
        self.setMinimumSize(900, 600)
        self.setAttribute(Qt.WA_TranslucentBackground, False)

        # 根容器负责渐变背景
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(0)
        root = QFrame()
        root.setObjectName("RootBackground")
        outer.addWidget(root)

        rl = QHBoxLayout(root)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(14)

        # ---------------- 侧边栏 ----------------
        self.sidebar = Sidebar(db, theme, settings)
        self.sidebar.nav_changed.connect(self.switch_page)
        self.sidebar.tag_filter_changed.connect(self._on_tag_filter)
        self.sidebar.settings_clicked.connect(self.open_settings)
        rl.addWidget(self.sidebar)

        # ---------------- 内容区 ----------------
        content = QVBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(12)
        rl.addLayout(content, 1)

        # 头部
        header = QHBoxLayout()
        header.setSpacing(10)
        self.lbl_title = QLabel("列表")
        self.lbl_title.setObjectName("H1")
        header.addWidget(self.lbl_title)
        self.lbl_sub = QLabel("")
        self.lbl_sub.setObjectName("Sub")
        header.addWidget(self.lbl_sub)
        header.addStretch(1)

        self.search = QLineEdit()
        self.search.setObjectName("SearchBox")
        self.search.setPlaceholderText("🔍  搜索标题 / 备注 / 标签")
        self.search.setFixedWidth(220)
        self.search.textChanged.connect(self._on_search)
        header.addWidget(self.search)

        btn_new = QPushButton("＋ 新建提醒")
        btn_new.setObjectName("Primary")
        btn_new.setCursor(Qt.PointingHandCursor)
        btn_new.clicked.connect(self.new_task)
        header.addWidget(btn_new)

        btn_theme = QPushButton("🌓")
        btn_theme.setObjectName("Ghost")
        btn_theme.setFixedSize(34, 32)
        btn_theme.setToolTip("切换深色 / 浅色")
        btn_theme.setCursor(Qt.PointingHandCursor)
        btn_theme.clicked.connect(self.toggle_theme)
        header.addWidget(btn_theme)
        content.addLayout(header)

        # 三视图
        self.stack = QStackedWidget()
        self.list_view = ListView(db, scheduler, theme, settings)
        self.calendar_view = CalendarView(db, scheduler, theme, settings)
        self.stats_view = StatsView(db, scheduler, theme, settings)
        # 班级通告页：需要同步对象；没传时不崩（冒烟测试里就不传）
        self.class_view = ClassView(db, class_sync, theme, settings) \
            if class_sync is not None else None
        # 老师端管理页：只有填了管理员密钥才会显示（见 _update_admin_nav）
        self.admin_view = AdminView(settings, theme)
        self._page_order = ["list", "calendar", "stats"]
        self._page_titles = {"list": "列表", "calendar": "日历", "stats": "统计"}
        views = [self.list_view, self.calendar_view, self.stats_view]
        if self.class_view is not None:
            self._page_order.append("class")
            self._page_titles["class"] = "班级"
            views.append(self.class_view)
            self.class_view.on_open_setup = self.open_settings
        self._page_order.append("admin")
        self._page_titles["admin"] = "班级管理"
        views.append(self.admin_view)
        for v in views:
            self.stack.addWidget(v)
        content.addWidget(self.stack, 1)

        self._update_admin_nav()

        # 列表页的「清除筛选」按钮要同步回侧边栏的标签选中状态
        self.list_view.on_filter_cleared = self._on_filter_cleared_from_list

        # 视图信号
        for view in (self.list_view, self.calendar_view):
            view.task_edit.connect(self.edit_task)
            view.task_toggled.connect(self._on_card_toggled)
        self.list_view.task_snooze.connect(self._on_snooze)
        self.list_view.task_delete.connect(self._on_delete)
        self.list_view.task_skip.connect(self._on_skip)
        self.calendar_view.day_selected.connect(self._on_day_selected)

        # 调度器信号
        self.scheduler.due.connect(self.on_due)
        self.scheduler.refreshed.connect(self.refresh_all)
        self.scheduler.day_changed.connect(lambda _d: self.refresh_all())

        # 每分钟刷新一次"今天"信息（跨天/进度）
        self._clock = QTimer(self)
        self._clock.setInterval(60_000)
        self._clock.timeout.connect(self.refresh_all)
        self._clock.start()

        self.switch_page(settings.get("last_page", "list"))
        QTimer.singleShot(80, self.refresh_all)

    # ------------------------------------------------------------------ 页面
    def switch_page(self, key: str):
        # 「管理」页没配管理员密钥时不给进：侧边栏已经藏了，这里再挡一道，
        # 防止 last_page 记着 "admin" 或者别处直接调进来。
        if key == "admin" and not self._admin_ready():
            key = "list"
        if key not in self._page_order:
            key = "list"
        idx = self._page_order.index(key)
        self.stack.setCurrentIndex(idx)
        self.sidebar.set_active(key)
        self.lbl_title.setText(self._page_titles.get(key, ""))
        self.settings.set("last_page", key)
        # 淡入 + 右移 8px 的切换动画
        w = self.stack.currentWidget()
        if w is not None:
            eff = QGraphicsOpacityEffect(w)
            w.setGraphicsEffect(eff)
            a = QPropertyAnimation(eff, b"opacity", w)
            a.setDuration(cfg.ANIM_NORMAL)
            a.setStartValue(0.0)
            a.setEndValue(1.0)
            a.setEasingCurve(QEasingCurve.OutCubic)
            a.finished.connect(lambda ww=w: ww.setGraphicsEffect(None))
            a.start()
            w._fade_anim = a
        self.refresh_all()

    def _on_search(self, text: str):
        self.list_view.set_search(text)
        if self.stack.currentIndex() != 0 and text:
            self.switch_page("list")

    def _on_tag_filter(self, tags: list):
        self.list_view.set_tag_filter(tags)
        self.refresh_all()

    def _on_day_selected(self, d):
        self.lbl_sub.setText(f"（{d.month}月{d.day}日）")

    def _on_filter_cleared_from_list(self):
        """列表页点了「清除筛选」：清掉侧边栏的标签选中态并保存。"""
        self.sidebar._tags = []
        self.settings.set("tag_filter", [])
        self.sidebar.refresh_tags()
        self.refresh_all()

    # ------------------------------------------------------------------ 刷新
    def refresh_all(self):
        self.sidebar.refresh_overview()
        key = self._page_order[self.stack.currentIndex()] \
            if 0 <= self.stack.currentIndex() < len(self._page_order) else "list"
        if key == "list":
            self.list_view.refresh()
        elif key == "calendar":
            self.calendar_view.refresh()
        elif key == "stats":
            self.stats_view.refresh()
        elif key == "class" and self.class_view is not None:
            self.class_view.refresh()
        elif key == "admin":
            self.admin_view.refresh()
        # 侧边栏「班级」项显示未读数
        self._update_class_badge()
        st = stats_mod.day_stats(self.db, date.today())
        self.lbl_sub.setText(f"今天 {st['done']}/{st['total']} 已完成")

    # ------------------------------------------------------------------ 管理页
    def _admin_ready(self) -> bool:
        """填了服务器地址和管理员密钥，才算这台机器是"老师机"。"""
        return bool((self.settings.get("class_admin_key", "") or "").strip()
                    and (self.settings.get("class_server_url", "") or "").strip())

    def _update_admin_nav(self):
        """按有没有管理员密钥，显示/隐藏侧边栏的「管理」。"""
        btn = self.sidebar.nav_buttons.get("admin")
        if btn is None:
            return
        ready = self._admin_ready()
        btn.setVisible(ready)
        if not ready:
            # 密钥被清掉了（比如老师把设置改回学生机）：如果人正停在管理页，
            # 得把他挪回列表，否则停在一个再也进不去的页面上。
            current = self._page_order[self.stack.currentIndex()] \
                if 0 <= self.stack.currentIndex() < len(self._page_order) else "list"
            if current == "admin":
                self.switch_page("list")

    def _update_class_badge(self):
        """给侧边栏的「班级」加未读数，例如「📣   班级 (2)」。"""
        btn = self.sidebar.nav_buttons.get("class")
        if btn is None:
            return
        n = 0
        if self.class_view is not None:
            try:
                n = self.class_view.sync.unread_count()
            except Exception:
                n = 0
        btn.setText(f"  📣   班级" + (f" ({n})" if n else ""))

    # ------------------------------------------------------------------ 提醒
    def on_due(self, task, when, catchup: bool):
        """调度器说该提醒了 —— 弹窗 / 系统通知 / 提示音。

        注意：**自定义弹窗和系统通知只出其中一个**，不要两个同时弹。
        两者都开时以前会同时出现两张提醒（右下角卡片 + Windows 横幅），
        用户会以为程序出 bug 了（"为什么到了时间有 2 个弹窗"）。
        优先级：自定义弹窗 > 系统通知 —— 因为卡片上有「完成 / 稍后 / 跳过」
        按钮，比只能看看的系统横幅更有用。
        """
        popup_on = (self.settings.get("popup_enabled", True)
                    and self._popups is not None)
        notify_on = self.settings.get("system_notify_enabled", True)

        if popup_on:
            self._popups.show_task(task, catchup)
        elif notify_on:
            # 只有在没启用自定义弹窗时，才用系统通知兜底
            self.notify_system(task, catchup)

        if self.settings.get("sound_enabled", True):
            self.play_sound()
        self.refresh_all()

    def notify_system(self, task, catchup: bool = False):
        tray = getattr(QApplication.instance(), "_memo_tray", None)
        if tray is None:
            return
        title = ("补提醒 · " if catchup else "") + (task.title or "提醒")
        bits = [task.times_text()]
        if task.category:
            bits.append(task.category)
        if task.note:
            n = task.note.strip().splitlines()[0]
            bits.append(n[:60])
        tray.showMessage(title, " · ".join(bits),
                         make_icon(128, self.theme.c), 8000)

    def play_sound(self):
        """播放提示音。用 Windows 自带的 winsound（异步播放，不卡界面），
        不需要 QtMultimedia —— PySide6-Essentials 里没有那个模块。"""
        from ..reminder import play_chime
        play_chime()

    # ------------------------------------------------------------------ 任务操作
    def new_task(self):
        dlg = dialogs.TaskEditDialog(self.theme, None, self.db, self)
        if dlg.exec() == 1:
            t = dlg.result_task()
            t.created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.db.add_task(t)
            for tag in t.tags:
                self.db.add_tag(tag)
            self.db.add_category(t.category)
            fresh = self.db.get_task(t.id)
            if fresh:
                nxt = compute_next_at(fresh)
                self.db.update_fields(fresh.id, next_at=nxt)
            self.sidebar.refresh_tags()
            self.refresh_all()
            self.scheduler.reschedule_all()

    def edit_task(self, task_id: int):
        t = self.db.get_task(task_id)
        if not t:
            return
        dlg = dialogs.TaskEditDialog(self.theme, t, self.db, self)
        res = dlg.exec()
        if res == 1:
            new = dlg.result_task()
            self.db.update_task(new)
            for tag in new.tags:
                self.db.add_tag(tag)
            self.db.add_category(new.category)
            fresh = self.db.get_task(new.id)
            if fresh:
                self.db.update_fields(fresh.id, next_at=compute_next_at(fresh))
            self.sidebar.refresh_tags()
            self.scheduler.reschedule_all()
            self.refresh_all()
        elif res == 2:
            self._on_delete(task_id)

    def _on_card_toggled(self, task_id: int, checked: bool):
        t = self.db.get_task(task_id)
        if not t:
            return
        if checked:
            planned = self.scheduler.planned_date_of(t) or date.today()
            if planned > date.today():
                planned = date.today()
            self.scheduler.complete(t, planned=planned)
        else:
            # ★ 取消完成交给调度器统一处理：清完成记录、把"下次提醒"放回今天、
            #   并保证不因此补弹（细节见 ReminderScheduler.unmark_done_reschedule）。
            #   以前这里用 compute_next_at() 重排，它会把"今天已经过去的时间点"
            #   算回来，任务立刻变成"已过期"，调度器马上补弹一次 ——
            #   用户看到的就是"取消勾选后弹窗又冒出来"。
            self.scheduler.unmark_done_reschedule(t)
        self.refresh_all()

    def _on_snooze(self, task_id: int):
        t = self.db.get_task(task_id)
        if t:
            self.scheduler.snooze(t)
            self.refresh_all()

    def _on_skip(self, task_id: int):
        t = self.db.get_task(task_id)
        if t:
            self.scheduler.skip_once(t)
            self.refresh_all()

    def _on_delete(self, task_id: int):
        t = self.db.get_task(task_id)
        if not t:
            return
        # 老师发的班级任务不给删：同学误删了就会漏做作业，而且下次同步又会回来。
        # 想让它消失只能由老师撤回（撤回后下次同步会自动删掉）。
        if t.is_class_task:
            QMessageBox.information(
                self, "这条是老师发的",
                f"「{t.title}」是老师通过班级通告发下来的任务，不能删除。\n\n"
                "如果你想让它不再提醒，可以点它的圆圈标记完成；\n"
                "如果这条已经不需要了，请让老师撤回。")
            return
        if QMessageBox.question(self, "删除提醒",
                                f"确定删除「{t.title}」吗？\n删除后无法恢复。",
                                QMessageBox.Yes | QMessageBox.No,
                                QMessageBox.No) == QMessageBox.Yes:
            self.db.delete_task(task_id)
            self.scheduler.mark_pending_done_externally(task_id)
            self.refresh_all()

    # ------------------------------------------------------------------ 其他
    def toggle_theme(self):
        cur = self.theme.mode
        self.settings.set("theme", "dark" if cur == "light" else "light")
        self.theme.apply()
        self.apply_theme(self.theme.c)
        self.refresh_all()

    def apply_theme(self, c: dict):
        self.setWindowIcon(make_icon(128, c))
        self.sidebar.apply_theme(c)
        tray = getattr(QApplication.instance(), "_memo_tray", None)
        if tray is not None:
            tray.setIcon(make_icon(128, c))

    def open_settings(self):
        dlg = SettingsDialog(self.theme, self.settings, self.db, self)
        if dlg.exec() == 1:
            self.settings = dlg.settings
            self.sidebar.refresh_tags()
            self.refresh_all()
            self.scheduler.reschedule_all()

    def closeEvent(self, e):
        if self._force_quit or not self.settings.get("minimize_to_tray", True):
            e.accept()
            return
        e.ignore()
        self.hide()
        tray = getattr(QApplication.instance(), "_memo_tray", None)
        if tray is not None and not self.settings.get("_tray_hint_shown", False):
            tray.showMessage(cfg.APP_NAME, "我还在后台运行，到点会提醒你。\n"
                                           "双击托盘图标可以打开窗口。",
                             make_icon(128, self.theme.c), 5000)
            self.settings.set("_tray_hint_shown", True)

    def force_quit(self):
        self._force_quit = True
        self.close()


# ===========================================================================
# 设置对话框
# ===========================================================================

class _ProbeSettings:
    """「测试连接」时用的临时设置：只在内存里，不写用户配置文件。

    这样同学可以先试通再保存；万一填错了也不会把坏配置存进去。
    device_id 复用真实配置里的值，保证服务端认得出是同一台设备。
    """

    def __init__(self, url: str, key: str, name: str, real=None):
        self._d = {"class_server_url": url, "class_join_key": key,
                   "class_student_name": name, "class_device_id":
                       (real or {}).get("class_device_id", "") or "probe-device",
                   "class_enabled": True}

    def get(self, k, d=None):
        return self._d.get(k, d)

    def set(self, k, v):
        self._d[k] = v

    def update(self, **kw):
        self._d.update(kw)

    def save(self):
        pass


class SettingsDialog(QDialog):
    """设置：外观 / 提醒方式 / 新任务默认值 / 节假日 / 数据 / 开机自启。"""

    def __init__(self, theme, settings, db, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.settings = settings
        self.db = db
        self.setWindowTitle("设置")
        self.setMinimumSize(560, 620)
        from ..theme import make_icon
        self.setWindowIcon(make_icon(64, theme.c))
        self.setObjectName("RootBackground")
        self.setStyleSheet(f"QDialog#RootBackground {{ background: {theme.c['bg_grad_a']}; }}")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 14)
        outer.setSpacing(12)

        head = QLabel("设置")
        head.setObjectName("H1")
        outer.addWidget(head)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        bl = QVBoxLayout(body)
        bl.setContentsMargins(0, 0, 8, 0)
        bl.setSpacing(16)

        c = theme.c

        # ---------------- 外观 ----------------
        bl.addWidget(self._sec("外观", c))
        row = QHBoxLayout()
        row.addWidget(QLabel("主题"))
        self.cmb_theme = QComboBox()
        self.cmb_theme.addItem("跟随系统", "auto")
        self.cmb_theme.addItem("浅色（青色天蓝）", "light")
        self.cmb_theme.addItem("深色", "dark")
        i = self.cmb_theme.findData(settings.get("theme", "auto"))
        self.cmb_theme.setCurrentIndex(max(0, i))
        row.addWidget(self.cmb_theme, 1)
        bl.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("窗口关闭时"))
        self.cmb_close = QComboBox()
        self.cmb_close.addItem("最小化到托盘，继续提醒", True)
        self.cmb_close.addItem("直接退出程序", False)
        self.cmb_close.setCurrentIndex(
            0 if settings.get("minimize_to_tray", True) else 1)
        row.addWidget(self.cmb_close, 1)
        bl.addLayout(row)

        # ---------------- 提醒方式 ----------------
        bl.addWidget(self._div(c))
        bl.addWidget(self._sec("提醒方式", c))
        self.chk_popup = QCheckBox("弹出右下角提醒卡片（带「完成 / 稍后 / 跳过」按钮）")
        self.chk_popup.setChecked(bool(settings.get("popup_enabled", True)))
        bl.addWidget(self.chk_popup)

        # 说明两者不会同时出现，避免用户以为弹了两个是 bug
        hint_popup = QLabel("上面两项只生效其中一个：开了提醒卡片就用卡片，"
                            "关掉卡片才用系统通知。默认只用卡片。")
        hint_popup.setObjectName("Muted")
        hint_popup.setWordWrap(True)
        bl.addWidget(hint_popup)

        self.chk_system = QCheckBox("改用 Windows 系统通知"
                                   "（默认关闭；开启后会进系统通知中心留记录）")
        self.chk_system.setChecked(bool(settings.get("system_notify_enabled", False)))
        bl.addWidget(self.chk_system)

        self.chk_sound = QCheckBox("播放提示音")
        self.chk_sound.setChecked(bool(settings.get("sound_enabled", True)))
        bl.addWidget(self.chk_sound)

        btn_try = QPushButton("试听提示音")
        btn_try.setObjectName("Ghost")
        btn_try.clicked.connect(self._try_sound)
        bl.addWidget(btn_try, 0, Qt.AlignLeft)

        row = QHBoxLayout()
        row.addWidget(QLabel("弹窗自动消失"))
        self.spin_auto = QSpinBox()
        self.spin_auto.setRange(0, 120)
        self.spin_auto.setSuffix(" 秒")
        self.spin_auto.setSpecialValueText("不自动消失")
        self.spin_auto.setValue(int(settings.get("popup_auto_close_sec", 20)))
        row.addWidget(self.spin_auto)
        row.addSpacing(16)
        row.addWidget(QLabel("最多同屏"))
        self.spin_stack = QSpinBox()
        self.spin_stack.setRange(1, 8)
        self.spin_stack.setSuffix(" 张")
        self.spin_stack.setValue(int(settings.get("popup_max_stack", 3)))
        row.addWidget(self.spin_stack)
        row.addStretch(1)
        bl.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("「稍后提醒」默认"))
        self.cmb_snooze = QComboBox()
        for m in (5, 10, 15, 30, 60):
            self.cmb_snooze.addItem(f"{m} 分钟", m)
        i = self.cmb_snooze.findData(int(settings.get("snooze_minutes", 10)))
        self.cmb_snooze.setCurrentIndex(max(0, i))
        row.addWidget(self.cmb_snooze)
        row.addStretch(1)
        bl.addLayout(row)

        # ---------------- 新任务默认值 ----------------
        bl.addWidget(self._div(c))
        bl.addWidget(self._sec("新建提醒时的默认值", c))
        row = QHBoxLayout()
        row.addWidget(QLabel("默认优先级"))
        self.cmb_prio = QComboBox()
        self.cmb_prio.addItems(cfg.PRIORITIES)
        self.cmb_prio.setCurrentText(settings.get("default_priority", "中"))
        row.addWidget(self.cmb_prio)
        row.addSpacing(16)
        row.addWidget(QLabel("默认分类"))
        self.cmb_cat = QComboBox()
        self.cmb_cat.addItems([x["name"] for x in db.categories()])
        self.cmb_cat.setCurrentText(settings.get("default_category", "工作"))
        row.addWidget(self.cmb_cat)
        row.addStretch(1)
        bl.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("默认提前提醒"))
        self.cmb_lead = QComboBox()
        for m in cfg.LEAD_CHOICES:
            self.cmb_lead.addItem("准点" if m == 0 else f"提前 {m} 分钟", m)
        i = self.cmb_lead.findData(int(settings.get("default_lead_minutes", 0)))
        self.cmb_lead.setCurrentIndex(max(0, i))
        row.addWidget(self.cmb_lead)
        row.addStretch(1)
        bl.addLayout(row)

        # ---------------- 节假日 ----------------
        bl.addWidget(self._div(c))
        bl.addWidget(self._sec("法定节假日", c))
        info = QLabel("内置 2025–2026 年国务院公布的放假与调休安排；"
                      "2027 年安排公布后可在此手动补充，或在代码 app/holidays.py 里更新。")
        info.setObjectName("Muted")
        info.setWordWrap(True)
        bl.addWidget(info)

        self.chk_defer = QCheckBox("任务落在节假日时，自动顺延到下一个工作日")
        self.chk_defer.setChecked(bool(settings.get("defer_on_holiday", True)))
        bl.addWidget(self.chk_defer)

        self.chk_work_weekend = QCheckBox("我周末也要上班（周末算工作日，不自动跳过）")
        self.chk_work_weekend.setChecked(bool(settings.get("work_weekend", False)))
        bl.addWidget(self.chk_work_weekend)

        bl.addWidget(QLabel("额外放假日期（每行一个，格式 2027-01-01）"))
        self.txt_holidays = QTextEdit()
        self.txt_holidays.setFixedHeight(64)
        extra = self.settings.get("extra_holidays", []) or []
        self.txt_holidays.setPlainText("\n".join(extra))
        bl.addWidget(self.txt_holidays)

        bl.addWidget(QLabel("额外调休上班日（每行一个）"))
        self.txt_makeup = QTextEdit()
        self.txt_makeup.setFixedHeight(64)
        self.txt_makeup.setPlainText("\n".join(self.settings.get("extra_makeup", []) or []))
        bl.addWidget(self.txt_makeup)

        # ---------------- 班级接入 ----------------
        bl.addWidget(self._div(c))
        bl.addWidget(self._sec("班级通告（老师发、同学收）", c))
        tip = QLabel("把老师给你的「服务器地址」和「接入密钥」填在这里，"
                     "就能收到老师发的通告和班级任务。不填也能正常用，"
                     "只是收不到班级内容。")
        tip.setObjectName("Muted")
        tip.setWordWrap(True)
        bl.addWidget(tip)

        row = QHBoxLayout()
        row.addWidget(QLabel("服务器地址"))
        self.edit_class_url = QLineEdit(settings.get("class_server_url", ""))
        self.edit_class_url.setPlaceholderText("例如 http://192.168.1.5:8765")
        row.addWidget(self.edit_class_url, 1)
        bl.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("接入密钥"))
        self.edit_class_key = QLineEdit(settings.get("class_join_key", ""))
        self.edit_class_key.setPlaceholderText("老师发给你的那串码，例如 MEMO-XXXXXX")
        row.addWidget(self.edit_class_key, 1)
        bl.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("你的名字（可留空）"))
        self.edit_class_name = QLineEdit(settings.get("class_student_name", ""))
        self.edit_class_name.setPlaceholderText("填了老师能看到谁接入了；留空也能收通告")
        row.addWidget(self.edit_class_name, 1)
        bl.addLayout(row)

        # 管理员密钥：只有老师填。填了侧边栏才会出现「管理」，用来发通告/发任务。
        # 这里要写清楚"同学别填"，否则学生拿到老师密钥填进来就成了老师身份。
        row = QHBoxLayout()
        row.addWidget(QLabel("管理员密钥（只有老师填）"))
        self.edit_class_admin = QLineEdit(settings.get("class_admin_key", ""))
        self.edit_class_admin.setEchoMode(QLineEdit.Password)
        self.edit_class_admin.setPlaceholderText("同学留空！填了会多出「管理」页，能发内容")
        row.addWidget(self.edit_class_admin, 1)
        self.btn_admin_eye = QPushButton("显示")
        self.btn_admin_eye.setObjectName("Ghost")
        self.btn_admin_eye.setCursor(Qt.PointingHandCursor)
        self.btn_admin_eye.setCheckable(True)
        self.btn_admin_eye.setFixedWidth(56)
        self.btn_admin_eye.toggled.connect(self._toggle_admin_key_echo)
        row.addWidget(self.btn_admin_eye)
        bl.addLayout(row)

        crow = QHBoxLayout()
        self.btn_class_test = QPushButton("测试连接")
        self.btn_class_test.setCursor(Qt.PointingHandCursor)
        self.btn_class_test.clicked.connect(self._test_class_connection)
        crow.addWidget(self.btn_class_test)
        self.lbl_class_test = QLabel("")
        self.lbl_class_test.setObjectName("Sub")
        self.lbl_class_test.setWordWrap(True)
        crow.addWidget(self.lbl_class_test, 1)
        bl.addLayout(crow)

        # ---------------- 启动 ----------------
        bl.addWidget(self._div(c))
        bl.addWidget(self._sec("启动", c))
        self.chk_autostart = QCheckBox("开机自动启动（静默启动到托盘，保证早上能提醒你）")
        self.chk_autostart.setChecked(bool(settings.get("autostart", True)))
        bl.addWidget(self.chk_autostart)

        # ---------------- 数据 ----------------
        bl.addWidget(self._div(c))
        bl.addWidget(self._sec("数据", c))
        lbl_path = QLabel(f"数据文件：{cfg.DB_PATH}")
        lbl_path.setObjectName("Muted")
        lbl_path.setWordWrap(True)
        bl.addWidget(lbl_path)

        drow = QHBoxLayout()
        for text, fn in (("立即备份", self._do_backup),
                         ("导出 CSV", lambda: self._do_export("csv")),
                         ("导出 JSON", lambda: self._do_export("json")),
                         ("打开数据文件夹", self._open_folder)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            drow.addWidget(b)
        drow.addStretch(1)
        bl.addLayout(drow)

        self.lbl_data_msg = QLabel("")
        self.lbl_data_msg.setObjectName("Sub")
        bl.addWidget(self.lbl_data_msg)

        btn_reset = QPushButton("清空所有提醒（危险）")
        btn_reset.setObjectName("Danger")
        btn_reset.clicked.connect(self._do_reset)
        bl.addWidget(btn_reset, 0, Qt.AlignLeft)

        bl.addStretch(1)

        # ---------------- 底部 ----------------
        outer.addWidget(self._div(c))
        btns = QHBoxLayout()
        lbl_ver = QLabel(f"{cfg.APP_NAME} v{cfg.APP_VERSION}")
        lbl_ver.setObjectName("Muted")
        btns.addWidget(lbl_ver, 1)
        b_cancel = QPushButton("取消")
        b_cancel.clicked.connect(self.reject)
        btns.addWidget(b_cancel)
        b_ok = QPushButton("保存")
        b_ok.setObjectName("Primary")
        b_ok.setDefault(True)
        b_ok.clicked.connect(self._save)
        btns.addWidget(b_ok)
        outer.addLayout(btns)

    # ------------------------------------------------------------------
    def _sec(self, text: str, c: dict) -> QLabel:
        lb = QLabel(text)
        lb.setStyleSheet(f"color: {c['accent_press']}; font-size: 13px; font-weight: 600;")
        return lb

    def _div(self, c: dict) -> QFrame:
        f = QFrame()
        f.setFixedHeight(1)
        f.setStyleSheet(f"background: {c['divider']};")
        return f

    def _toggle_admin_key_echo(self, shown: bool):
        """管理员密钥默认打码，老师要核对时点一下「显示」。"""
        self.edit_class_admin.setEchoMode(
            QLineEdit.Normal if shown else QLineEdit.Password)
        self.btn_admin_eye.setText("隐藏" if shown else "显示")

    def _try_sound(self):
        from ..reminder import play_chime
        if not play_chime():
            self.lbl_data_msg.setText("提示音播放失败（系统不支持）")

    def _test_class_connection(self):
        """用当前填写的地址/密钥试连一次，结果只显示在这行文字里。"""
        from ..class_sync import ClassSync
        url = self.edit_class_url.text().strip()
        key = self.edit_class_key.text().strip()
        if not url or not key:
            self.lbl_class_test.setText("请先填服务器地址和接入密钥")
            return
        # 用临时设置在内存里试，不写进用户配置（点保存才写）
        probe = ClassSync(self.db, self.settings)
        probe.settings = _ProbeSettings(url, key,
                                        self.edit_class_name.text().strip(),
                                        self.settings.as_dict())
        self.lbl_class_test.setText("正在连接…")
        QApplication.processEvents()
        res = probe.test_connection()
        if res.ok:
            self.lbl_class_test.setText("✓ 连接成功，密钥有效")
        else:
            self.lbl_class_test.setText(f"✗ {res.error}")

    def _do_backup(self):
        p = self.db.backup()
        self.lbl_data_msg.setText(f"已备份到：{p}" if p else "备份失败")

    def _do_export(self, kind: str):
        from .. import stats as stats_mod
        default = str(cfg.DATA_DIR / f"memo_export_{date.today():%Y%m%d}.{kind}")
        filt = "CSV 文件 (*.csv)" if kind == "csv" else "JSON 文件 (*.json)"
        path, _ = QFileDialog.getSaveFileName(self, "导出", default, filt)
        if not path:
            return
        try:
            n = (stats_mod.export_csv if kind == "csv" else stats_mod.export_json)(self.db, path)
            self.lbl_data_msg.setText(f"已导出 {n} 条到 {path}")
        except Exception as e:
            self.lbl_data_msg.setText(f"导出失败：{e}")

    def _open_folder(self):
        import subprocess
        cfg.DATA_DIR.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["explorer", str(cfg.DATA_DIR)])

    def _do_reset(self):
        if QMessageBox.warning(self, "清空数据",
                               "这会删除所有提醒和完成记录，且无法恢复。\n"
                               "建议先点「立即备份」。确定继续吗？",
                               QMessageBox.Yes | QMessageBox.No,
                               QMessageBox.No) == QMessageBox.Yes:
            self.db.backup()
            self.db.reset_all()
            self.lbl_data_msg.setText("已清空（清空前自动做了一次备份）")

    # ------------------------------------------------------------------
    def _lines(self, widget) -> list[str]:
        out = []
        for line in widget.toPlainText().splitlines():
            line = line.strip().replace("/", "-")
            if not line:
                continue
            try:
                date.fromisoformat(line)
                out.append(line)
            except Exception:
                pass
        return out

    def _save(self):
        from ..holidays import reload_calendar
        from ..autostart import set_autostart

        extra_h = self._lines(self.txt_holidays)
        extra_m = self._lines(self.txt_makeup)
        work_weekend = self.chk_work_weekend.isChecked()
        self.settings.update(
            theme=self.cmb_theme.currentData(),
            minimize_to_tray=self.cmb_close.currentData(),
            popup_enabled=self.chk_popup.isChecked(),
            system_notify_enabled=self.chk_system.isChecked(),
            sound_enabled=self.chk_sound.isChecked(),
            popup_auto_close_sec=self.spin_auto.value(),
            popup_max_stack=self.spin_stack.value(),
            snooze_minutes=self.cmb_snooze.currentData(),
            default_priority=self.cmb_prio.currentText(),
            default_category=self.cmb_cat.currentText(),
            default_lead_minutes=self.cmb_lead.currentData(),
            defer_on_holiday=self.chk_defer.isChecked(),
            work_weekend=work_weekend,
            extra_holidays=extra_h,
            extra_makeup=extra_m,
            autostart=self.chk_autostart.isChecked(),
            # ---- 班级接入 ----
            class_server_url=self.edit_class_url.text().strip().rstrip("/"),
            class_join_key=self.edit_class_key.text().strip(),
            class_student_name=self.edit_class_name.text().strip(),
            class_admin_key=self.edit_class_admin.text().strip(),
            class_enabled=bool(self.edit_class_url.text().strip()
                               and self.edit_class_key.text().strip()),
        )
        # 新接入时要生成设备标识，否则服务端看不到这台设备
        from ..class_sync import ClassSync
        ClassSync(self.db, self.settings)._ensure_device_id()
        reload_calendar(extra_h, extra_m, work_weekend)
        set_autostart(self.chk_autostart.isChecked())
        self.theme.settings = self.settings
        self.theme.apply()
        self.accept()

        # 保存后刷新主窗口：「管理」页可能要出现/消失，出现了就顺手拉一次数据，
        # 别让老师进去看见一片空白还以为没发出去。
        win = self.parent()
        if win is not None and hasattr(win, "_update_admin_nav"):
            win._update_admin_nav()
            if win._admin_ready():
                win.admin_view.refresh()
