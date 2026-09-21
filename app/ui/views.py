"""三个主视图：列表 / 日历 / 统计。

它们都通过 main_window 传入的 db / scheduler / theme 与数据打交道，
自己不发 SQL（除了通过 stats 模块）。
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (QButtonGroup, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QMenu, QPushButton, QScrollArea, QSizePolicy,
                               QStackedWidget, QVBoxLayout, QWidget)

from .. import config as cfg
from .. import stats as stats_mod
from ..holidays import CALENDAR
from ..recurrence import holds_on
from .widgets import (EmptyState, RingProgress, TaskCard, chip, friendly_date, hline,
                      soft_shadow)


# ===========================================================================
# 列表视图
# ===========================================================================

class ListView(QWidget):
    task_toggled = Signal(int, bool)
    task_edit = Signal(int)
    task_snooze = Signal(int)
    task_delete = Signal(int)
    task_skip = Signal(int)

    def __init__(self, db, scheduler, theme, settings, parent=None):
        super().__init__(parent)
        self.db = db
        self.scheduler = scheduler
        self.theme = theme
        self.settings = settings
        self.search_text = ""
        self.tag_filter: list[str] = list(settings.get("tag_filter") or [])

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        # --- 过滤栏 ---
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.btn_all = chip("全部", True)
        self.btn_todo = chip("未完成", False)
        self.btn_done = chip("已完成", False)
        for b in (self.btn_all, self.btn_todo, self.btn_done):
            b.clicked.connect(self._on_filter_clicked)
            bar.addWidget(b)
        bar.addStretch(1)
        self.lbl_count = QLabel("")
        self.lbl_count.setObjectName("Muted")
        bar.addWidget(self.lbl_count)
        root.addLayout(bar)

        # --- 滚动区 ---
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.holder = QWidget()
        self.scroll.setWidget(self.holder)
        self.vbox = QVBoxLayout(self.holder)
        self.vbox.setContentsMargins(2, 2, 8, 12)
        self.vbox.setSpacing(8)
        self.vbox.addStretch(1)
        root.addWidget(self.scroll, 1)

        self._filter = "all"

    # ------------------------------------------------------------------
    def set_search(self, text: str):
        self.search_text = (text or "").strip().lower()
        self.refresh()

    def set_tag_filter(self, tags: list[str]):
        self.tag_filter = list(tags or [])
        self.refresh()

    def _on_filter_clicked(self):
        sender = self.sender()
        for b in (self.btn_all, self.btn_todo, self.btn_done):
            b.setChecked(b is sender)
        self._filter = {"全部": "all", "未完成": "todo", "已完成": "done"}[sender.text()]
        self.refresh()

    # ------------------------------------------------------------------
    def _matches(self, t) -> bool:
        if self.search_text:
            hay = f"{t.title} {t.note} {t.category} {' '.join(t.tags)}".lower()
            if self.search_text not in hay:
                return False
        if self.tag_filter:
            if not set(self.tag_filter) & set(t.tags or []):
                return False
        if self._filter == "todo" and t.done:
            return False
        if self._filter == "done" and not t.done:
            return False
        return True

    def refresh(self):
        c = self.theme.c
        # 清空
        while self.vbox.count() > 1:
            item = self.vbox.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()

        tasks = [t for t in self.db.all_tasks() if self._matches(t)]
        today = date.today()

        # 分组：逾期 / 今天 / 明天 / 本周 / 更远 / 已完成 / 暂停
        groups: dict[str, list] = {}
        order = ["逾期", "今天", "明天", "本周内", "以后", "已停用", "已完成"]
        for t in tasks:
            key = self._group_of(t, today)
            groups.setdefault(key, []).append(t)

        total_shown = 0
        for key in order:
            items = groups.get(key)
            if not items:
                continue
            items.sort(key=lambda x: (x.next_at or "9999", -cfg.PRIORITIES.index(x.priority)
                                      if x.priority in cfg.PRIORITIES else 0))
            self.vbox.insertWidget(self.vbox.count() - 1, self._group_header(key, len(items), c))
            for t in items:
                card = TaskCard(t, self.theme)
                card.toggled.connect(self.task_toggled)
                card.edit_requested.connect(self.task_edit)
                card.snooze_requested.connect(self.task_snooze)
                card.delete_requested.connect(self.task_delete)
                card.skip_requested.connect(self.task_skip)
                self.vbox.insertWidget(self.vbox.count() - 1, card)
                total_shown += 1

        if total_shown == 0:
            if self.search_text:
                self.vbox.insertWidget(0, EmptyState("🔍", "没找到匹配的提醒",
                                                     "换个关键词试试"))
            elif self._filter == "done":
                self.vbox.insertWidget(0, EmptyState("🌱", "还没有完成过的事情",
                                                     "完成一件后这里会出现记录"))
            else:
                self.vbox.insertWidget(0, EmptyState(
                    "☕", "今天还没有安排", "点右上角「＋ 新建提醒」加一件要做的事"))

        self.btn_all.setText(f"全部 {len(self.db.all_tasks())}")
        self.lbl_count.setText(f"当前显示 {total_shown} 条")

    def _group_of(self, t, today: date) -> str:
        if t.done:
            return "已完成"
        if not t.enabled:
            return "已停用"
        nd = t.next_date
        if nd is None:
            return "以后"
        if nd < today:
            return "逾期"
        if nd == today:
            return "今天"
        if nd == today + timedelta(days=1):
            return "明天"
        if nd <= today + timedelta(days=7 - today.weekday()):
            return "本周内"
        return "以后"

    def _group_header(self, key: str, n: int, c: dict) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(4, 8, 4, 0)
        lay.setSpacing(8)
        dot_color = {"逾期": c["danger"], "今天": c["accent"], "明天": c["accent2"],
                     "本周内": c["text_sub"], "以后": c["text_mute"],
                     "已完成": c["p_low"], "已停用": c["text_mute"]}.get(key, c["text_sub"])
        dot = QLabel("●")
        dot.setStyleSheet(f"color: {dot_color}; font-size: 9px;")
        lay.addWidget(dot)
        lb = QLabel(key)
        lb.setObjectName("GroupHeader")
        lay.addWidget(lb)
        cnt = QLabel(str(n))
        cnt.setObjectName("Muted")
        lay.addWidget(cnt)
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background: {c['divider']};")
        lay.addWidget(line, 1)
        return w


# ===========================================================================
# 日历视图
# ===========================================================================

class CalendarView(QWidget):
    task_edit = Signal(int)
    task_toggled = Signal(int, bool)
    day_selected = Signal(object)

    def __init__(self, db, scheduler, theme, settings, parent=None):
        super().__init__(parent)
        self.db = db
        self.scheduler = scheduler
        self.theme = theme
        self.settings = settings
        self.today = date.today()
        self.cursor = date(self.today.year, self.today.month, 1)
        self.selected = self.today

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        # ---------------- 左：月历 ----------------
        left = QVBoxLayout()
        left.setSpacing(12)
        left.setContentsMargins(0, 0, 0, 0)

        head = QHBoxLayout()
        head.setSpacing(2)
        self.btn_prev = QPushButton("‹")
        self.btn_prev.setObjectName("CalNav")
        self.btn_prev.setFixedSize(26, 28)
        self.btn_prev.clicked.connect(lambda: self._shift_month(-1))
        self.btn_next = QPushButton("›")
        self.btn_next.setObjectName("CalNav")
        self.btn_next.setFixedSize(26, 28)
        self.btn_next.clicked.connect(lambda: self._shift_month(1))
        self.lbl_month = QLabel()
        self.lbl_month.setObjectName("H2")
        self.lbl_month.setMinimumWidth(130)
        head.addWidget(self.btn_prev)
        head.addWidget(self.lbl_month)
        head.addWidget(self.btn_next)
        head.addStretch(1)

        btn_today = QPushButton("回到今天")
        btn_today.setObjectName("Ghost")
        btn_today.setFixedHeight(28)
        btn_today.clicked.connect(self._goto_today)
        head.addWidget(btn_today)
        head.addSpacing(12)

        # 图例放在头部右侧，不占垂直空间，也不会被挤出屏幕
        for txt, col in (("今天", theme.c["accent"]), ("有任务", theme.c["accent2"]),
                         ("已完成", theme.c["p_low"]), ("休/班", theme.c["danger"])):
            d = QLabel("●")
            d.setStyleSheet(f"color: {col}; font-size: 9px;")
            t = QLabel(txt)
            t.setObjectName("Muted")
            head.addWidget(d)
            head.addWidget(t)
            head.addSpacing(6)
        left.addLayout(head)

        grid_holder = QWidget()
        grid_holder.setObjectName("CalendarGrid")
        self.grid = QGridLayout(grid_holder)
        # 紧凑一点：这样常见的窗口高度下 6 行日历正好放下，不用滚动
        self.grid.setSpacing(4)
        self.grid.setContentsMargins(0, 2, 0, 8)   # 底部留白，免得最后一行被裁

        # 包一层滚动区：窗口太矮时日历可以滚动，而不是把最后一行切掉
        self.grid_scroll = QScrollArea()
        self.grid_scroll.setWidgetResizable(True)
        self.grid_scroll.setFrameShape(QFrame.NoFrame)
        self.grid_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # 正常情况放得下就不出现滚动条；窗口被拉得很矮时也能滚，不会把最后一行切掉
        self.grid_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.grid_scroll.setWidget(grid_holder)
        left.addWidget(self.grid_scroll, 1)

        leftw = QWidget()
        leftw.setLayout(left)
        leftw.setMinimumWidth(620)
        root.addWidget(leftw, 5)

        # ---------------- 右：当天任务 ----------------
        right = QVBoxLayout()
        right.setSpacing(8)
        self.lbl_day = QLabel()
        self.lbl_day.setObjectName("H2")
        right.addWidget(self.lbl_day)
        self.lbl_day_sub = QLabel()
        self.lbl_day_sub.setObjectName("Sub")
        right.addWidget(self.lbl_day_sub)

        panel = QFrame()
        panel.setObjectName("Panel")
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(10, 10, 6, 10)
        self.day_scroll = QScrollArea()
        self.day_scroll.setWidgetResizable(True)
        self.day_scroll.setFrameShape(QFrame.NoFrame)
        self.day_holder = QWidget()
        self.day_scroll.setWidget(self.day_holder)
        self.day_vbox = QVBoxLayout(self.day_holder)
        self.day_vbox.setContentsMargins(0, 0, 0, 0)
        self.day_vbox.setSpacing(8)
        self.day_vbox.addStretch(1)
        pl.addWidget(self.day_scroll)
        right.addWidget(panel, 1)

        rightw = QWidget()
        rightw.setLayout(right)
        rightw.setMinimumWidth(300)
        root.addWidget(rightw, 2)

        self._cells: list[QPushButton] = []
        self.rebuild()

    # ------------------------------------------------------------------
    def _shift_month(self, delta: int):
        y, m = self.cursor.year, self.cursor.month + delta
        if m < 1:
            y, m = y - 1, 12
        elif m > 12:
            y, m = y + 1, 1
        self.cursor = date(y, m, 1)
        self.rebuild()

    def _goto_today(self):
        self.today = date.today()
        self.cursor = date(self.today.year, self.today.month, 1)
        self.selected = self.today
        self.rebuild()

    def refresh(self):
        self.today = date.today()
        self.rebuild()

    # ------------------------------------------------------------------
    def _tasks_of_day(self, d: date) -> list:
        out = []
        for t in self.db.all_tasks():
            if not t.enabled and not t.done_date == d.isoformat():
                continue
            if holds_on(t, d):
                out.append(t)
            elif t.done_date == d.isoformat():
                out.append(t)
        return out

    def rebuild(self):
        c = self.theme.c
        self.lbl_month.setText(f"{self.cursor.year} 年 {self.cursor.month} 月")

        # 清空网格
        while self.grid.count():
            item = self.grid.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()

        start_monday = self.settings.get("week_start_monday", True)
        headers = (["一", "二", "三", "四", "五", "六", "日"] if start_monday
                   else ["日", "一", "二", "三", "四", "五", "六"])
        for i, h in enumerate(headers):
            lb = QLabel(h)
            lb.setAlignment(Qt.AlignCenter)
            lb.setObjectName("Muted")
            lb.setFixedHeight(20)
            self.grid.addWidget(lb, 0, i)

        first = self.cursor
        days_in = calendar.monthrange(first.year, first.month)[1]
        first_weekday = first.weekday()          # 0=周一
        if not start_monday:
            first_weekday = (first_weekday + 1) % 7
        row = 1
        col = first_weekday

        # 上月尾巴
        prev_last = first - timedelta(days=1)
        for i in range(first_weekday):
            d = prev_last - timedelta(days=first_weekday - 1 - i)
            self.grid.addWidget(self._make_cell(d, other_month=True), row, i)
        col = first_weekday

        for day in range(1, days_in + 1):
            d = date(first.year, first.month, day)
            self.grid.addWidget(self._make_cell(d, other_month=False), row, col)
            col += 1
            if col > 6:
                col = 0
                row += 1
        # 下月头，补满 6 行（避免月份切换时高度跳动）
        nxt = date(first.year, first.month, days_in) + timedelta(days=1)
        while row <= 6:
            self.grid.addWidget(self._make_cell(nxt, other_month=True), row, col)
            nxt += timedelta(days=1)
            col += 1
            if col > 6:
                col = 0
                row += 1

        for r in range(1, 7):
            self.grid.setRowStretch(r, 1)
            # 6 行 × (44 + 4 间距) + 表头 ≈ 310px，常见窗口高度下不用滚动
            self.grid.setRowMinimumHeight(r, 44)
        for cc in range(7):
            self.grid.setColumnStretch(cc, 1)
            self.grid.setColumnMinimumWidth(cc, 54)

        self._refresh_day_panel()

    def _make_cell(self, d: date, other_month: bool) -> QWidget:
        c = self.theme.c
        cell = QWidget()
        cell.setMinimumHeight(44)
        cell.setMinimumWidth(54)
        lay = QVBoxLayout(cell)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        btn = QPushButton()
        btn.setObjectName("CalDay")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        btn.clicked.connect(lambda _=False, dd=d: self.select_day(dd))

        tasks = self._tasks_of_day(d)
        undone = [t for t in tasks if not (t.done and t.done_date == d.isoformat())]
        all_done = bool(tasks) and not undone
        is_today = (d == self.today)
        if d == self.selected:
            btn.setObjectName("CalDaySelected")
        elif is_today:
            btn.setObjectName("CalDayToday")
        elif other_month:
            btn.setObjectName("CalDayOther")

        # 日期数字旁边的小角标：节日名（元/春/清/劳/端/中/国）、休、班
        badge = ""
        if not other_month:
            if CALENDAR.is_holiday(d):
                name = CALENDAR.holiday_name(d)
                badge = {"元旦": "元", "春节": "春", "除夕": "除", "清明": "清",
                         "劳动节": "劳", "端午": "端", "中秋": "中",
                         "国庆": "国"}.get(name, name[:1] or "休")
            elif CALENDAR.is_makeup_workday(d):
                badge = "班"

        # 在按钮里画：日期数字 + 任务圆点 + 节日/休/班角标
        btn.setText("")
        btn.setProperty("day", d.isoformat())
        btn.setProperty("dots", [bool(undone), all_done, len(tasks)])
        btn.setProperty("badge", badge)
        btn.setProperty("is_today", is_today)
        btn.setProperty("other", other_month)
        btn.paintEvent = self._cell_painter(btn, d, undone, all_done, other_month)  # type: ignore
        lay.addWidget(btn)
        return cell

    def _cell_painter(self, btn, d, undone, all_done, other_month):
        c = self.theme.c
        badges = "" if other_month else (btn.property("badge") or "")

        def painter(_event):
            p = QPainter(btn)
            p.setRenderHint(QPainter.Antialiasing)
            w, h = btn.width(), btn.height()
            selected = btn.objectName() == "CalDaySelected"

            # 底色
            if selected:
                g = QLinearGradient(0, 0, w, h)
                g.setColorAt(0, QColor(c["accent"]))
                g.setColorAt(1, QColor(c["accent2"]))
                p.setBrush(QBrush(g))
                p.setPen(Qt.NoPen)
                p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), 10, 10)
            elif btn.objectName() == "CalDayToday":
                p.setBrush(QBrush(QColor(c["accent_soft"])))
                pen = QPen(QColor(c["accent"]))
                pen.setWidthF(2.0)
                p.setPen(pen)
                p.drawRoundedRect(QRectF(1, 1, w - 2, h - 2), 10, 10)
            elif not other_month:
                p.setBrush(QBrush(QColor(c["card"])))
                pen = QPen(QColor(c["border"]))
                pen.setWidthF(1.0)
                p.setPen(pen)
                p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), 10, 10)

            # 日期数字
            f = QFont(btn.font())
            f.setPointSizeF(10.5)
            if d == self.today:
                f.setBold(True)
            p.setFont(f)
            if selected:
                p.setPen(QColor("#FFFFFF"))
            elif other_month:
                p.setPen(QColor(c["grid_other_month"]))
            elif CALENDAR.is_holiday(d) or (CALENDAR.is_weekend(d) and not CALENDAR.is_makeup_workday(d)):
                p.setPen(QColor(c["holiday"] if CALENDAR.is_holiday(d) else c["weekend"]))
            else:
                p.setPen(QColor(c["text"]))
            p.drawText(QRectF(0, 3, w, 16), Qt.AlignHCenter | Qt.AlignTop, str(d.day))

            # 「休 / 班 / 节日」角标：字号小、右对齐、用省略号防止溢出格子
            if badges:
                bf = QFont(btn.font())
                bf.setPointSizeF(6.8)
                p.setFont(bf)
                p.setPen(QColor("#FFFFFF" if selected else
                                (c["danger"] if (badges in ("休",) or CALENDAR.is_holiday(d))
                                 else c["p_mid"])))
                rect = QRectF(w - 30, 3, 27, 11)
                fm = p.fontMetrics()
                txt = fm.elidedText(badges, Qt.ElideRight, int(rect.width()))
                p.drawText(rect, Qt.AlignRight | Qt.AlignVCenter, txt)

            # 任务圆点
            n = len(undone)
            if n or all_done:
                cols = min(3, max(1, n + (1 if all_done and not n else 0)))
                dot_r = 2.6
                gap = 6.0
                total_w = cols * gap
                x0 = (w - total_w) / 2 + gap / 2
                y = h - 10
                for i in range(cols):
                    if all_done and not n:
                        col = QColor(c["p_low"])
                    else:
                        cols_list = [c["accent"], c["accent2"], c["p_low"]]
                        col = QColor("#FFFFFF" if selected else cols_list[i % 3])
                    p.setBrush(QBrush(col))
                    p.setPen(Qt.NoPen)
                    p.drawEllipse(QRectF(x0 + i * gap - dot_r, y - dot_r,
                                         dot_r * 2, dot_r * 2))
                if n > 3:
                    p.setPen(QColor("#FFFFFF" if selected else c["text_sub"]))
                    tf = QFont(btn.font())
                    tf.setPointSizeF(6.5)
                    p.setFont(tf)
                    p.drawText(QRectF(w - 22, h - 16, 20, 12),
                               Qt.AlignRight | Qt.AlignVCenter, f"+{n - 3}")
            p.end()

        return painter

    def select_day(self, d: date):
        self.selected = d
        if d.month != self.cursor.month or d.year != self.cursor.year:
            self.cursor = date(d.year, d.month, 1)
            self.rebuild()
        else:
            # 只更新选中态：整块重建最简单也最稳
            self.rebuild()
        self.day_selected.emit(d)

    def _refresh_day_panel(self):
        d = self.selected
        c = self.theme.c
        self.lbl_day.setText(friendly_date(d))
        badge = CALENDAR.label(d)
        sub = ""
        if badge:
            sub = "法定节假日" if badge == "休" or CALENDAR.is_holiday(d) else "调休上班日"
            if CALENDAR.is_holiday(d) and CALENDAR.holiday_name(d):
                sub = CALENDAR.holiday_name(d)
        tasks = self._tasks_of_day(d)
        self.lbl_day_sub.setText(f"{sub} · 共 {len(tasks)} 件" if sub else f"共 {len(tasks)} 件")

        while self.day_vbox.count() > 1:
            item = self.day_vbox.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()

        if not tasks:
            self.day_vbox.insertWidget(0, EmptyState("🍃", "这天没有安排", "可以点「＋ 新建提醒」加一件"))
            return
        tasks.sort(key=lambda t: (t.next_at or "", t.id or 0))
        for t in tasks:
            card = TaskCard(t, self.theme, compact=True)
            card.edit_requested.connect(self.task_edit)
            card.toggled.connect(self.task_toggled)
            card.btn_snooze.setVisible(False)
            card.btn_skip.setVisible(False)
            card.btn_del.setVisible(False)
            self.day_vbox.insertWidget(self.day_vbox.count() - 1, card)


# ===========================================================================
# 统计视图
# ===========================================================================

class MiniBarChart(QWidget):
    """一周完成率柱状图（自绘，没有第三方图表库）。"""

    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.data: list[dict] = []
        self.setMinimumHeight(150)

    def set_data(self, daily: list[dict]):
        self.data = daily or []
        self.update()

    def paintEvent(self, _):
        c = self.theme.c
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        if not self.data:
            p.setPen(QColor(c["text_mute"]))
            p.drawText(self.rect(), Qt.AlignCenter, "暂无数据")
            p.end()
            return

        pad_l, pad_b, pad_t = 8, 26, 18
        n = len(self.data)
        gap = 12
        bw = max(10.0, (w - pad_l * 2 - gap * (n - 1)) / n)
        base_y = h - pad_b
        max_h = h - pad_b - pad_t

        for i, d in enumerate(self.data):
            x = pad_l + i * (bw + gap)
            # 底槽
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(QColor(c["today_ring_bg"])))
            p.drawRoundedRect(QRectF(x, pad_t, bw, max_h), bw / 2, bw / 2)

            # 柱子
            ratio = max(0.0, min(1.0, d.get("rate", 0.0)))
            bh = max_h * ratio if d.get("total") else 0
            if bh > 2:
                g = QLinearGradient(0, base_y - bh, 0, base_y)
                g.setColorAt(0, QColor(c["accent2"]))
                g.setColorAt(1, QColor(c["accent"]))
                p.setBrush(QBrush(g))
                p.drawRoundedRect(QRectF(x, base_y - bh, bw, bh), bw / 2, bw / 2)

            # 完成情况：显示 "完成数/应做数"，比只显示完成数更好理解
            if d.get("total"):
                p.setPen(QColor(c["text"]))
                f = QFont(self.font())
                f.setPointSizeF(8)
                p.setFont(f)
                p.drawText(QRectF(x - 8, base_y - bh - 17, bw + 16, 15),
                           Qt.AlignCenter, f"{d.get('done', 0)}/{d.get('total', 0)}")

            # 星期标签
            f = QFont(self.font())
            f.setPointSizeF(8.5)
            f.setBold(bool(d.get("is_today")))
            p.setFont(f)
            p.setPen(QColor(c["accent_press"] if d.get("is_today") else c["text_sub"]))
            p.drawText(QRectF(x - 6, base_y + 4, bw + 12, 16), Qt.AlignCenter,
                       d.get("label", ""))
        p.end()


class DonutChart(QWidget):
    """分类占比环形图。"""

    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.items: list[dict] = []
        self.setMinimumHeight(170)

    def set_data(self, items: list[dict]):
        self.items = items or []
        self.update()

    def paintEvent(self, _):
        c = self.theme.c
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        if not self.items:
            p.setPen(QColor(c["text_mute"]))
            p.drawText(self.rect(), Qt.AlignCenter, "这段时间还没有完成记录")
            p.end()
            return

        size = min(h - 16, w * 0.42)
        rect = QRectF(14, (h - size) / 2, size, size)
        thick = max(12.0, size * 0.16)

        start = 90 * 16
        total = sum(i["count"] for i in self.items) or 1
        for it in self.items:
            span = -int(360 * 16 * (it["count"] / total))
            pen = QPen(QColor(it["color"]))
            pen.setWidthF(thick)
            pen.setCapStyle(Qt.FlatCap)
            p.setPen(pen)
            p.drawArc(rect.adjusted(thick / 2, thick / 2, -thick / 2, -thick / 2),
                      start, span)
            start += span

        # 中间总数
        f = QFont(self.font())
        f.setPointSizeF(15)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(c["text"]))
        p.drawText(rect, Qt.AlignCenter, str(total))
        f.setPointSizeF(8)
        f.setBold(False)
        p.setFont(f)
        p.setPen(QColor(c["text_sub"]))
        p.drawText(rect.adjusted(0, 22, 0, 22), Qt.AlignCenter, "完成")

        # 右侧图例
        lx = rect.right() + 22
        ly = (h - len(self.items) * 22) / 2
        for it in self.items[:6]:
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(QColor(it["color"])))
            p.drawRoundedRect(QRectF(lx, ly + 5, 10, 10), 3, 3)
            f = QFont(self.font())
            f.setPointSizeF(9)
            p.setFont(f)
            p.setPen(QColor(c["text"]))
            p.drawText(QRectF(lx + 16, ly, 90, 20), Qt.AlignVCenter | Qt.AlignLeft,
                       it["name"])
            p.setPen(QColor(c["text_sub"]))
            p.drawText(QRectF(lx + 108, ly, w - lx - 116, 20),
                       Qt.AlignVCenter | Qt.AlignRight,
                       f"{it['count']} · {int(it['ratio']*100)}%")
            ly += 22
        p.end()


class StatCard(QFrame):
    """统计页里的小方块。"""

    def __init__(self, title: str, value: str, sub: str, theme, color_key: str = "accent",
                 parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        soft_shadow(self, theme.c["shadow"], blur=12, dy=2, alpha=60)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(2)
        t = QLabel(title)
        t.setObjectName("Muted")
        lay.addWidget(t)
        v = QLabel(value)
        v.setObjectName("BigNumber")
        v.setStyleSheet(f"color: {theme.c[color_key]}; font-size: 26px; font-weight: 600;")
        lay.addWidget(v)
        s = QLabel(sub)
        s.setObjectName("Sub")
        s.setWordWrap(True)
        lay.addWidget(s)
        self._value_label = v
        self._sub_label = s

    def set_value(self, v: str):
        self._value_label.setText(v)

    def set_sub(self, text: str):
        self._sub_label.setText(text)


class StatsView(QWidget):
    def __init__(self, db, scheduler, theme, settings, parent=None):
        super().__init__(parent)
        self.db = db
        self.scheduler = scheduler
        self.theme = theme
        self.settings = settings
        self.period_days = 7

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        holder = QWidget()
        scroll.setWidget(holder)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll)

        lay = QVBoxLayout(holder)
        lay.setContentsMargins(2, 2, 8, 14)
        lay.setSpacing(14)

        # 顶栏：时间范围
        top = QHBoxLayout()
        title = QLabel("统计")
        title.setObjectName("H2")
        top.addWidget(title)
        top.addStretch(1)
        self.range_btns = []
        grp = QButtonGroup(self)
        for label, days in (("本周", 7), ("近两周", 14), ("近一月", 30), ("近三月", 90)):
            b = chip(label, days == 7)
            b.clicked.connect(lambda _=False, d=days: self._set_range(d))
            grp.addButton(b)
            top.addWidget(b)
            self.range_btns.append((days, b))
        lay.addLayout(top)

        # 数字卡片行
        cards = QHBoxLayout()
        cards.setSpacing(12)
        self.card_rate = StatCard("整体完成率", "0%", "", theme, "accent")
        self.card_streak = StatCard("连续打卡", "0 天", "每天都完成至少一件事", theme, "accent2")
        self.card_done = StatCard("共完成", "0 件", "", theme, "p_low")
        self.card_ontime = StatCard("准时率", "0%", "按计划日期完成的比例", theme, "p_mid")
        for c in (self.card_rate, self.card_streak, self.card_done, self.card_ontime):
            cards.addWidget(c, 1)
        lay.addLayout(cards)

        # 本周柱状
        wk = QFrame()
        wk.setObjectName("Card")
        soft_shadow(wk, theme.c["shadow"], blur=14, dy=2, alpha=60)
        wl = QVBoxLayout(wk)
        wl.setContentsMargins(16, 14, 16, 10)
        hrow = QHBoxLayout()
        h = QLabel("本周每日完成情况")
        h.setObjectName("CardTitle")
        hrow.addWidget(h)
        hrow.addStretch(1)
        self.lbl_week_sum = QLabel("")
        self.lbl_week_sum.setObjectName("Sub")
        hrow.addWidget(self.lbl_week_sum)
        wl.addLayout(hrow)
        self.bar = MiniBarChart(theme)
        wl.addWidget(self.bar)
        lay.addWidget(wk)

        # 分类 + 星期习惯
        two = QHBoxLayout()
        two.setSpacing(12)

        catf = QFrame()
        catf.setObjectName("Card")
        soft_shadow(catf, theme.c["shadow"], blur=14, dy=2, alpha=60)
        cl = QVBoxLayout(catf)
        cl.setContentsMargins(16, 14, 16, 12)
        ch = QLabel("分类占比")
        ch.setObjectName("CardTitle")
        cl.addWidget(ch)
        self.donut = DonutChart(theme)
        cl.addWidget(self.donut)
        two.addWidget(catf, 3)

        habitf = QFrame()
        habitf.setObjectName("Card")
        soft_shadow(habitf, theme.c["shadow"], blur=14, dy=2, alpha=60)
        hl = QVBoxLayout(habitf)
        hl.setContentsMargins(16, 14, 16, 12)
        hh = QLabel("这几个月你哪天最能干")
        hh.setObjectName("CardTitle")
        hl.addWidget(hh)
        self.week_bars = QVBoxLayout()
        self.week_bars.setSpacing(6)
        hl.addLayout(self.week_bars)
        hl.addStretch(1)
        two.addWidget(habitf, 2)
        lay.addLayout(two)

        # 提示
        self.lbl_tip = QLabel()
        self.lbl_tip.setObjectName("Muted")
        self.lbl_tip.setWordWrap(True)
        lay.addWidget(self.lbl_tip)
        lay.addStretch(1)

    def _set_range(self, days: int):
        self.period_days = days
        for d, b in self.range_btns:
            b.setChecked(d == days)
        self.refresh()

    def refresh(self):
        today = date.today()
        start = today - timedelta(days=self.period_days - 1)
        tasks = self.db.all_tasks()

        logs = stats_mod.completion_log_between(self.db, start, today)
        n_done = len(logs)

        # 应做件数：逐天累加（只用「这天确实有任务」的日子做分母）
        expected = 0
        for i in range(self.period_days):
            d = start + timedelta(days=i)
            expected += len(stats_mod.expected_on(self.db, d, tasks))

        # 完成率口径统一：本周（同一份数据）也用「已完成 / 应做」，
        # 这样顶部卡片和柱状图右下角的数字一定是一致的。
        wk = stats_mod.week_stats(self.db, today, tasks)
        rate = (wk["done"] / wk["total"]) if wk["total"] else 0.0

        self.card_rate.set_value(f"{int(rate*100)}%")
        self.card_rate.set_sub(f"{wk['done']} / {wk['total']} 件（本周应做）")
        self.card_done.set_value(f"{n_done} 件")
        self.card_done.set_sub(f"近 {self.period_days} 天的完成记录")
        streak = stats_mod.streak_days(self.db, tasks)
        self.card_streak.set_value(f"{streak} 天")
        ov = stats_mod.overdue_stats(self.db, self.period_days)
        self.card_ontime.set_value(f"{int(ov['on_time_rate']*100)}%")

        self.bar.set_data(wk["daily"])
        self.lbl_week_sum.setText(f"本周合计 {wk['done']} / {wk['total']} 件"
                                  f"　完成率 {int(rate*100)}%")

        self.donut.set_data(stats_mod.category_breakdown(self.db, start, today))

        # 星期习惯
        while self.week_bars.count():
            item = self.week_bars.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        for row in stats_mod.weekday_completion(self.db, weeks=max(4, self.period_days // 7)):
            self.week_bars.addWidget(self._habit_row(row))

        tip = []
        if streak >= 3:
            tip.append(f"已经连续 {streak} 天完成任务，保持住！")
        if expected and rate < 0.5:
            tip.append("完成率偏低，是不是安排得太多了？可以试着每天只定 3 件重要的事。")
        if ov["late"] and ov["avg_overdue"] > 0:
            tip.append(f"平均逾 {ov['avg_overdue']:.1f} 天完成，考虑把提醒时间往前挪一点。")
        if not tip:
            tip.append("数据会随着你使用慢慢积累，坚持一周再回来看会更有意思。")
        self.lbl_tip.setText("　".join(tip))

    def _habit_row(self, row: dict) -> QWidget:
        c = self.theme.c
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lb = QLabel(row["label"])
        lb.setObjectName("Sub")
        lb.setFixedWidth(18)
        lay.addWidget(lb)

        bar_bg = QFrame()
        bar_bg.setFixedHeight(10)
        bar_bg.setStyleSheet(f"background: {c['today_ring_bg']}; border-radius: 5px;")
        bar_bg.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        inner = QFrame(bar_bg)
        inner.setStyleSheet(
            f"background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            f"stop:0 {c['accent']}, stop:1 {c['accent2']}); border-radius: 5px;")
        inner.setFixedHeight(10)

        def on_resize(_e, parent=bar_bg, ch=inner, r=row["ratio"]):
            ch.setGeometry(0, 0, max(2, int(parent.width() * r)), 10)
        bar_bg.resizeEvent = on_resize  # type: ignore
        lay.addWidget(bar_bg, 1)

        n = QLabel(str(row["count"]))
        n.setObjectName("Muted")
        n.setFixedWidth(24)
        n.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(n)
        return w
