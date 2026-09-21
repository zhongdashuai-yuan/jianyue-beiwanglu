"""★ 右下角自定义提醒弹窗。

设计要点（按"不打断、有体验感"来做）：
  * 无边框、置顶，但 setAttribute(WA_ShowWithoutActivating) —— 不会抢走你的输入焦点
  * 从屏幕右侧滑入 + 淡入（320ms OutCubic），点"稍后/完成"后滑出
  * 同屏最多叠 N 张（默认 3），多了排队
  * N 秒无操作自动淡出（任务不会被标完成，下一轮 tick 还会再提醒）
"""

from __future__ import annotations

import math

from PySide6.QtCore import (QEasingCurve, QPoint, QPropertyAnimation, QRect, Qt, QTimer,
                            Signal)
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QMenu,
                               QPushButton, QVBoxLayout, QWidget)

from .. import config as cfg


class Popup(QWidget):
    """单张提醒卡片。"""

    action_done = Signal(int)            # task_id
    action_snooze = Signal(int, int)     # task_id, 分钟
    action_skip = Signal(int)
    closed = Signal(int)

    WIDTH = 360
    MARGIN = 18
    GAP = 10

    def __init__(self, task, theme, catchup: bool = False,
                 snooze_minutes: int = 10, auto_close_sec: int = 20, parent=None):
        super().__init__(None)
        self.task = task
        self.theme = theme
        self.catchup = catchup
        self.snooze_minutes = snooze_minutes
        self._closing = False

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint |
                            Qt.Tool | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedWidth(self.WIDTH)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)      # 给阴影留位置
        self.frame = QFrame()
        self.frame.setObjectName("Popup")
        outer.addWidget(self.frame)

        lay = QVBoxLayout(self.frame)
        lay.setContentsMargins(16, 14, 14, 14)
        lay.setSpacing(9)

        c = theme.c

        # ---- 第一行：时间 + 优先级 + 关闭 ----
        row1 = QHBoxLayout()
        row1.setSpacing(8)
        if catchup:
            tag = QLabel("补")
            tag.setStyleSheet(
                f"color:#FFFFFF; background:{c['p_mid']}; border-radius:7px;"
                f"padding:0px 6px; font-size:11px; font-weight:600;")
            tag.setFixedHeight(18)
            row1.addWidget(tag)
            self.setWindowTitle("补提醒")

        lbl_time = QLabel(f"⏰ {task.times_text()}")
        lbl_time.setObjectName("PopupTime")
        row1.addWidget(lbl_time)

        if task.priority and task.priority != "低":
            pcolor = c[cfg.PRIORITY_COLOR_KEY.get(task.priority, "p_low")]
            pb = QLabel(task.priority)
            pb.setStyleSheet(f"color:{pcolor}; border:1px solid {pcolor};"
                             f"border-radius:8px; padding:0px 7px; font-size:11px;")
            pb.setFixedHeight(18)
            row1.addWidget(pb)

        row1.addStretch(1)
        btn_close = QPushButton("✕")
        btn_close.setObjectName("Ghost")
        btn_close.setFixedSize(24, 24)
        btn_close.setCursor(Qt.PointingHandCursor)
        btn_close.clicked.connect(lambda: self._close("auto_closed"))
        row1.addWidget(btn_close)
        lay.addLayout(row1)

        # ---- 标题 ----
        title = QLabel(task.title or "(无标题)")
        title.setObjectName("PopupTitle")
        title.setWordWrap(True)
        lay.addWidget(title)

        # ---- 备注 ----
        if task.note:
            note = task.note.strip()
            if len(note) > 120:
                note = note[:120] + "…"
            lbl_note = QLabel(note)
            lbl_note.setObjectName("Sub")
            lbl_note.setWordWrap(True)
            lay.addWidget(lbl_note)

        # ---- 重复说明 ----
        bits = [task.repeat_text()]
        if task.category:
            bits.append(task.category)
        if task.tags:
            bits.append("·".join(task.tags))
        meta = QLabel(" · ".join(bits))
        meta.setObjectName("Muted")
        lay.addWidget(meta)

        # ---- 按钮行 ----
        row3 = QHBoxLayout()
        row3.setSpacing(8)

        btn_done = QPushButton("完成")
        btn_done.setObjectName("Primary")
        btn_done.setFixedHeight(32)
        btn_done.setCursor(Qt.PointingHandCursor)
        btn_done.clicked.connect(lambda: self._act("done"))
        row3.addWidget(btn_done, 1)

        btn_snooze = QPushButton(f"稍后 {self.snooze_minutes} 分")
        btn_snooze.setFixedHeight(32)
        btn_snooze.setCursor(Qt.PointingHandCursor)
        btn_snooze.clicked.connect(lambda: self._act("snooze"))
        row3.addWidget(btn_snooze, 1)

        # 稍后时间可选
        btn_more = QPushButton("▾")
        btn_more.setFixedSize(28, 32)
        btn_more.setCursor(Qt.PointingHandCursor)
        btn_more.clicked.connect(lambda: self._snooze_menu(btn_more))
        row3.addWidget(btn_more)

        btn_skip = QPushButton("跳过")
        btn_skip.setObjectName("Ghost")
        btn_skip.setFixedHeight(32)
        btn_skip.setCursor(Qt.PointingHandCursor)
        btn_skip.clicked.connect(lambda: self._act("skip"))
        row3.addWidget(btn_skip)

        lay.addLayout(row3)

        # 自动关闭：底部一条细进度线（视觉上告诉用户"我还有多久消失"）
        self._auto_sec = int(auto_close_sec or 0)
        self._left = self._auto_sec
        self.life = QFrame()
        self.life.setFixedHeight(3)
        self.life.setStyleSheet(
            f"background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            f"stop:0 {c['accent']}, stop:1 {c['accent2']}); border-radius:1px;")
        lay.addWidget(self.life)
        self.life.setVisible(self._auto_sec > 0)

        if self._auto_sec > 0:
            self._tick = QTimer(self)
            self._tick.setInterval(200)
            self._tick.timeout.connect(self._tick_life)
            self._tick.start()

        # 滑入动画
        self._anim = QPropertyAnimation(self, b"pos", self)
        self._anim.setDuration(cfg.ANIM_SLOW)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

        self.adjustSize()

    # ------------------------------------------------------------------ 动画
    def slide_in(self, target: QPoint):
        self._target = target
        start = QPoint(target.x() + self.WIDTH // 2 + 30, target.y())
        self.move(start)
        self.setWindowOpacity(0.0)
        self.show()
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(cfg.ANIM_SLOW)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()
        self._anim.stop()
        self._anim.setStartValue(start)
        self._anim.setEndValue(target)
        self._anim.start()

    def move_to(self, target: QPoint, animate: bool = True):
        self._target = target
        if not animate:
            self.move(target)
            return
        self._anim.stop()
        self._anim.setStartValue(self.pos())
        self._anim.setEndValue(target)
        self._anim.start()

    def _act(self, kind: str):
        if kind == "done":
            self.action_done.emit(self.task.id)
        elif kind == "snooze":
            self.action_snooze.emit(self.task.id, self.snooze_minutes)
        elif kind == "skip":
            self.action_skip.emit(self.task.id)
        self._close(kind)

    def _snooze_menu(self, anchor: QWidget):
        m = QMenu(self)
        for mins in cfg.SNOOZE_CHOICES:
            m.addAction(f"{mins} 分钟后提醒",
                        lambda _=False, x=mins: self._act_snooze(x))
        m.addSeparator()
        for mins in (60, 120):
            m.addAction(f"{mins} 分钟后提醒",
                        lambda _=False, x=mins: self._act_snooze(x))
        m.addSeparator()
        m.addAction("明天这个时间", lambda: self._act_snooze(24 * 60))
        m.exec(anchor.mapToGlobal(QPoint(0, anchor.height())))

    def _act_snooze(self, minutes: int):
        self.snooze_minutes = minutes
        self._act("snooze")

    def _tick_life(self):
        self._left -= 0.2
        if self._left <= 0:
            self._close("auto_closed")
            return
        frac = max(0.0, self._left / self._auto_sec)
        self.life.setMaximumWidth(max(1, int(self.frame.width() * frac)))

    def _close(self, result: str = "closed"):
        if self._closing:
            return
        self._closing = True
        try:
            self._tick.stop()
        except Exception:
            pass
        self._result = result
        out = QPropertyAnimation(self, b"pos", self)
        out.setDuration(cfg.ANIM_FAST)
        out.setEasingCurve(QEasingCurve.InCubic)
        out.setStartValue(self.pos())
        out.setEndValue(QPoint(self.pos().x() + 40, self.pos().y()))
        fade = QPropertyAnimation(self, b"windowOpacity", self)
        fade.setDuration(cfg.ANIM_FAST)
        fade.setStartValue(self.windowOpacity())
        fade.setEndValue(0.0)
        out.finished.connect(self._finish_close)
        out.start()
        fade.start()
        self._out_anims = (out, fade)

    def _finish_close(self):
        self.closed.emit(self.task.id)
        self.hide()
        self.deleteLater()

    def enterEvent(self, e):
        # 鼠标放上去就暂停自动关闭，避免"正要点击它消失了"
        try:
            self._tick.stop()
            self.life.setVisible(False)
        except Exception:
            pass
        super().enterEvent(e)

    def leaveEvent(self, e):
        try:
            if self._auto_sec > 0 and not self._closing:
                self.life.setVisible(True)
                self._tick.start()
        except Exception:
            pass
        super().leaveEvent(e)


class PopupManager:
    """管一堆弹窗的位置、堆叠与队列。"""

    def __init__(self, theme, settings, on_done, on_snooze, on_skip):
        self.theme = theme
        self.settings = settings
        self.on_done = on_done
        self.on_snooze = on_snooze
        self.on_skip = on_skip
        self.active: list[Popup] = []
        self.queue: list[tuple] = []       # (task, catchup)

    # ------------------------------------------------------------------
    def show_task(self, task, catchup: bool = False):
        limit = int(self.settings.get("popup_max_stack", 3) or 3)
        if len(self.active) >= limit:
            self.queue.append((task, catchup))
            return
        p = Popup(task, self.theme, catchup=catchup,
                  snooze_minutes=int(self.settings.get("snooze_minutes", 10)),
                  auto_close_sec=int(self.settings.get("popup_auto_close_sec", 20)))
        p.action_done.connect(self._on_done)
        p.action_snooze.connect(self._on_snooze)
        p.action_skip.connect(self._on_skip)
        p.closed.connect(self._on_closed)
        self.active.append(p)
        self._reposition()
        p.slide_in(self._target_for(len(self.active) - 1))

    # ------------------------------------------------------------------
    def _screen_rect(self) -> QRect:
        scr = QGuiApplication.primaryScreen()
        if scr is None:
            return QRect(0, 0, 1920, 1080)
        # 用可用区域，自动避开任务栏
        return scr.availableGeometry()

    def _target_for(self, index: int) -> QPoint:
        """index 越大越新 -> 越靠下。最旧的排在最上面。"""
        r = self._screen_rect()
        # 统计比自己更新的那些卡片一共占多高（它们在下方）
        y_offset = 0
        for j in range(index + 1, len(self.active)):
            y_offset += self.active[j].sizeHint().height() + Popup.GAP
        bottom_edge = r.bottom() - Popup.MARGIN
        y = bottom_edge - y_offset - self.active[index].sizeHint().height()
        # 窗口自带 14px 阴影留白，所以右边要让出这一截，看起来才贴边 18px
        x = r.right() - Popup.MARGIN - Popup.WIDTH + 14
        return QPoint(x, y)

    def _reposition(self):
        for i, p in enumerate(self.active):
            p.move_to(self._target_for(i), animate=True)

    def _on_done(self, task_id: int):
        self.on_done(task_id)

    def _on_snooze(self, task_id: int, minutes: int):
        self.on_snooze(task_id, minutes)

    def _on_skip(self, task_id: int):
        self.on_skip(task_id)

    def _on_closed(self, task_id: int):
        self.active = [p for p in self.active if p.task.id != task_id]
        self._reposition()
        # 队列里的补上
        if self.queue:
            t, cu = self.queue.pop(0)
            QTimer.singleShot(200, lambda: self.show_task(t, cu))

    def close_all(self):
        for p in list(self.active):
            p._close("quit")
        self.active.clear()
        self.queue.clear()

    def count(self) -> int:
        return len(self.active) + len(self.queue)
