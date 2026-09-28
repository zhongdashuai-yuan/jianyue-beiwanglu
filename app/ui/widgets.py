"""通用界面零件：圆角卡片、柔和阴影、动画勾选框、环形进度、任务卡片、空状态。

配色全部从 theme.ThemeManager.c 里取，所以切深浅色时这些自绘控件会跟着变。
"""

from __future__ import annotations

from datetime import date, datetime

from PySide6.QtCore import (QEasingCurve, QEvent, QPropertyAnimation, QRectF, QSize,
                            Qt, Property, Signal)
from PySide6.QtGui import (QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath,
                           QPen, QPixmap)
from PySide6.QtWidgets import (QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel,
                               QPushButton, QSizePolicy, QVBoxLayout, QWidget)

from .. import config as cfg


# ---------------------------------------------------------------------------
# 柔和阴影
# ---------------------------------------------------------------------------

def soft_shadow(widget: QWidget, color: str, blur: int = cfg.CARD_SHADOW_BLUR,
                dy: int = cfg.CARD_SHADOW_OFFSET, alpha: int = 255):
    """给控件挂一层柔和阴影。Qt 的阴影只有一种颜色 + 模糊半径，正好符合"柔和"。"""
    eff = QGraphicsDropShadowEffect(widget)
    c = QColor(color)
    if c.alpha() == 255:
        c.setAlpha(alpha if alpha != 255 else 90)
    eff.setColor(c)
    eff.setBlurRadius(blur)
    eff.setOffset(0, dy)
    widget.setGraphicsEffect(eff)
    return eff


# ---------------------------------------------------------------------------
# 动画勾选框：勾选时有个"填充 + 打勾"的小动画，给即时反馈
# ---------------------------------------------------------------------------

class AnimatedCheck(QWidget):
    toggled = Signal(bool)

    def __init__(self, checked: bool = False, size: int = 22, parent=None):
        super().__init__(parent)
        self._checked = checked
        self._progress = 1.0 if checked else 0.0
        self._size = size
        self._hover = False
        self.setFixedSize(size + 4, size + 4)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)

        self._anim = QPropertyAnimation(self, b"progress", self)
        self._anim.setDuration(cfg.ANIM_FAST)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

        self._accent = "#12B5C9"
        self._border = "#CDE7F1"

    def apply_theme(self, c: dict):
        self._accent = c["accent"]
        self._border = c["border_strong"]
        self.update()

    # 动画属性
    def get_progress(self) -> float:
        return self._progress

    def set_progress(self, v: float):
        self._progress = max(0.0, min(1.0, v))
        self.update()

    progress = Property(float, get_progress, set_progress)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, v: bool, animate: bool = True, emit: bool = False):
        v = bool(v)
        if v == self._checked:
            return
        self._checked = v
        if animate:
            self._anim.stop()
            self._anim.setStartValue(self._progress)
            self._anim.setEndValue(1.0 if v else 0.0)
            self._anim.start()
        else:
            self.set_progress(1.0 if v else 0.0)
        if emit:
            self.toggled.emit(v)

    # 事件
    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.setChecked(not self._checked, emit=True)
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s = self._size
        x = (self.width() - s) / 2
        y = (self.height() - s) / 2
        rect = QRectF(x, y, s, s)
        r = s / 2

        # 底圈
        base = QColor(self._border)
        if self._hover:
            base = QColor(self._accent)
        pen = QPen(base)
        pen.setWidthF(2.0)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(rect.adjusted(1, 1, -1, -1))

        pr = self._progress
        if pr > 0.01:
            # 填充（内缩一点，看起来更精致）
            fill = QColor(self._accent)
            fill.setAlphaF(min(1.0, pr * 1.2))
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(fill))
            inner = rect.adjusted(2.0, 2.0, -2.0, -2.0)
            p.drawEllipse(inner)

            # 打勾：用裁剪做出"画出来"的效果
            if pr > 0.35:
                p.save()
                p.setClipRect(QRectF(rect.left(), rect.top(),
                                     rect.width() * min(1.0, (pr - 0.35) / 0.65),
                                     rect.height()))
                gp = QPen(QColor("#FFFFFF"))
                gp.setWidthF(max(1.8, s * 0.12))
                gp.setCapStyle(Qt.RoundCap)
                gp.setJoinStyle(Qt.RoundJoin)
                p.setPen(gp)
                path = QPainterPath()
                path.moveTo(rect.left() + s * 0.28, rect.top() + s * 0.52)
                path.lineTo(rect.left() + s * 0.44, rect.top() + s * 0.68)
                path.lineTo(rect.left() + s * 0.72, rect.top() + s * 0.34)
                p.drawPath(path)
                p.restore()
        p.end()


# ---------------------------------------------------------------------------
# 环形进度（今日完成率）
# ---------------------------------------------------------------------------

class RingProgress(QWidget):
    def __init__(self, parent=None, size: int = 108, thickness: int = 9):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._thickness = thickness
        self._value = 0.0
        self._display = 0.0
        self._accent = "#12B5C9"
        self._accent2 = "#4FC3E8"
        self._track = "#E4F1F6"
        self._text = "#1F3B45"
        self._sub = "#7E9AA6"
        self._label = "已完成"
        self._has_tasks = False      # False 时中间显示 "—" 而不是 0%

        self._anim = QPropertyAnimation(self, b"value", self)
        self._anim.setDuration(520)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

    def apply_theme(self, c: dict):
        self._accent = c["accent"]
        self._accent2 = c["accent2"]
        self._track = c["today_ring_bg"]
        self._text = c["text"]
        self._sub = c["text_sub"]
        self.update()

    def get_value(self) -> float:
        return self._display

    def set_value(self, v: float):
        self._display = max(0.0, min(1.0, v))
        self.update()

    value = Property(float, get_value, set_value)

    def set_ratio(self, ratio: float, animate: bool = True):
        ratio = max(0.0, min(1.0, ratio))
        self._value = ratio
        if animate:
            self._anim.stop()
            self._anim.setStartValue(self._display)
            self._anim.setEndValue(ratio)
            self._anim.start()
        else:
            self.set_value(ratio)

    def set_has_tasks(self, flag: bool):
        if flag != self._has_tasks:
            self._has_tasks = flag
            self.update()

    def set_label(self, text: str):
        self._label = text
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        t = self._thickness
        rect = QRectF(t / 2 + 1, t / 2 + 1,
                      self.width() - t - 2, self.height() - t - 2)

        pen = QPen(QColor(self._track))
        pen.setWidthF(t)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawArc(rect, 0, 360 * 16)

        if self._display > 0.002:
            g = QLinearGradient(rect.topLeft(), rect.bottomRight())
            g.setColorAt(0, QColor(self._accent))
            g.setColorAt(1, QColor(self._accent2))
            pen = QPen(QBrush(g), t)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            # 从 12 点方向开始顺时针
            p.drawArc(rect, 90 * 16, -int(360 * 16 * self._display))

        # 中间文字：一个任务都没有时显示 "—"，别显示 0% 让人以为失败
        f = QFont(self.font())
        f.setPointSizeF(20)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(self._text if self._has_tasks else self._sub))
        main_txt = f"{int(round(self._display * 100))}%" if self._has_tasks else "—"
        p.drawText(self.rect().adjusted(0, -8, 0, -8), Qt.AlignCenter, main_txt)

        f2 = QFont(self.font())
        f2.setPointSizeF(8.5)
        p.setFont(f2)
        p.setPen(QColor(self._sub))
        p.drawText(self.rect().adjusted(0, 34, 0, 34), Qt.AlignCenter, self._label)
        p.end()


# ---------------------------------------------------------------------------
# 任务卡片
# ---------------------------------------------------------------------------

def is_done_for(task, day=None) -> bool:
    """这条任务在指定日期算不算「已完成」。

    两个字段的分工：
        done       = 这一轮做完了没有
        done_date  = 哪一天做完的
    所以"今天算不算完成"必须两个一起看：done=1 但完成日期是以前某天，
    说明那是上一轮做完的，今天还没做 —— 例如每天的任务昨天打了勾，
    今天重开程序时它应该是空的，不该还画着勾。

    判定要统一用在三个地方，否则会出现"分组说未完成、卡片却画着勾"的矛盾：
        ListView._is_done_today()（分组/筛选）
        TaskCard（勾选框与完成时间）
        日历与统计（各自按天判断）
    """
    if not task.done:
        return False
    d = (day or date.today()).isoformat()
    if task.done_date and task.done_date < d:
        return False        # 那是以前做完的，这一天还没做
    return True


class TaskCard(QFrame):
    toggled = Signal(int, bool)      # task_id, 是否完成
    edit_requested = Signal(int)
    snooze_requested = Signal(int)
    delete_requested = Signal(int)
    skip_requested = Signal(int)

    def __init__(self, task, theme, parent=None, compact: bool = False):
        super().__init__(parent)
        self.task = task
        self.theme = theme
        self._hover = False
        self._compact = compact
        # 关键：勾选框和"已完成"样式都要按"今天是否完成"来画，
        # 不能直接用 task.done —— 那是"上一轮做完了"的意思，
        # 对每天/每周的重复任务来说，昨天打勾不代表今天已完成。
        done_today = is_done_for(task)

        self.setObjectName("CardDone" if done_today else "Card")
        self.setAttribute(Qt.WA_Hover, True)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._shadow = soft_shadow(self, theme.c["shadow"], blur=14, dy=2, alpha=70)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 12, 0)
        root.setSpacing(10)

        # 左侧优先级色条（自绘，见 paintEvent）
        self._bar_w = 4
        self._bar_color_key = cfg.PRIORITY_COLOR_KEY.get(task.priority, "p_low")

        # 勾选框
        self.check = AnimatedCheck(done_today)
        self.check.setFixedWidth(30)
        self.check.apply_theme(theme.c)
        self.check.toggled.connect(self._on_check)
        root.addSpacing(12)
        root.addWidget(self.check, 0, Qt.AlignVCenter)

        # 中间文字区
        mid = QVBoxLayout()
        mid.setContentsMargins(0, 9, 0, 9)
        mid.setSpacing(3)

        line1 = QHBoxLayout()
        line1.setSpacing(8)
        self.lbl_time = QLabel(task.times_text())
        self.lbl_time.setObjectName("Sub")
        self.lbl_time.setStyleSheet(
            f"color: {theme.c['accent']}; font-weight: 600; font-size: 13px;")
        line1.addWidget(self.lbl_time)

        self.lbl_title = QLabel(task.title or "(无标题)")
        self.lbl_title.setObjectName("CardTitleDone" if done_today else "CardTitle")
        self.lbl_title.setWordWrap(False)
        line1.addWidget(self.lbl_title, 1)

        if task.priority and task.priority != "低":
            pcolor = theme.c[cfg.PRIORITY_COLOR_KEY.get(task.priority, "p_low")]
            self.badge = QLabel(task.priority)
            self.badge.setStyleSheet(
                f"color: {pcolor}; border: 1px solid {pcolor};"
                f"border-radius: 8px; padding: 0px 7px; font-size: 11px;")
            self.badge.setFixedHeight(18)
            line1.addWidget(self.badge, 0, Qt.AlignVCenter)
        mid.addLayout(line1)

        line2 = QHBoxLayout()
        line2.setSpacing(8)
        meta_bits = [task.repeat_text()]
        if task.category:
            meta_bits.append(task.category)
        if task.tags:
            meta_bits.append("·".join(task.tags))
        if task.lead_minutes:
            meta_bits.append(f"提前{task.lead_minutes}分")
        if task.snooze_count:
            meta_bits.append(f"已延{task.snooze_count}次")
        if task.is_deferred():
            meta_bits.append("因假期顺延")

        self.lbl_meta = QLabel(" · ".join(meta_bits))
        self.lbl_meta.setObjectName("Muted")
        line2.addWidget(self.lbl_meta)

        # 老师发的任务标出来源，让同学一眼看出这条不是自己加的
        if task.is_class_task:
            badge = QLabel("老师")
            badge.setStyleSheet(
                f"color: {theme.c['accent_press']};"
                f" border: 1px solid {theme.c['accent']};"
                f" border-radius: 8px; padding: 0px 6px; font-size: 10px;")
            badge.setFixedHeight(16)
            line2.addWidget(badge)

        # 只有"今天完成"才显示完成时间。用 done_today 而不是 task.done ——
        # 否则昨天打了勾的每日任务，今天会显示"✓ 昨天17:18 完成"，看起来像今天做完了。
        if done_today and task.completed_at:
            self.lbl_done = QLabel(f"✓ {task.completed_at[11:16]} 完成")
            self.lbl_done.setStyleSheet(
                f"color: {theme.c['p_low']}; font-size: 11px;")
            line2.addWidget(self.lbl_done)

        line2.addStretch(1)

        # 悬浮才出现的快捷按钮
        self.btn_snooze = QPushButton("稍后")
        self.btn_snooze.setObjectName("Ghost")
        self.btn_snooze.setFixedHeight(24)
        self.btn_snooze.setCursor(Qt.PointingHandCursor)
        self.btn_snooze.clicked.connect(lambda: self.snooze_requested.emit(self.task.id))

        self.btn_skip = QPushButton("跳过本次")
        self.btn_skip.setObjectName("Ghost")
        self.btn_skip.setFixedHeight(24)
        self.btn_skip.setCursor(Qt.PointingHandCursor)
        self.btn_skip.clicked.connect(lambda: self.skip_requested.emit(self.task.id))

        self.btn_del = QPushButton("删除")
        self.btn_del.setObjectName("Ghost")
        self.btn_del.setFixedHeight(24)
        self.btn_del.setCursor(Qt.PointingHandCursor)
        # 同样：自定义颜色要带上完整基础样式，否则 Ghost 的样式会被覆盖
        self.btn_del.setStyleSheet(
            f"QPushButton#Ghost {{ color: {theme.c['danger']}; background: transparent;"
            f" border: 1px solid transparent; border-radius: 8px; padding: 4px 8px; }}"
            f"QPushButton#Ghost:hover {{ background: {theme.c['danger']}; color: #FFFFFF; }}")
        self.btn_del.clicked.connect(lambda: self.delete_requested.emit(self.task.id))

        for b in (self.btn_snooze, self.btn_skip, self.btn_del):
            b.setVisible(False)
            line2.addWidget(b)

        mid.addLayout(line2)

        if task.note and not compact:
            note = task.note.strip().replace("\n", "  ")
            if len(note) > 70:
                note = note[:70] + "…"
            self.lbl_note = QLabel(note)
            self.lbl_note.setObjectName("Muted")
            mid.addWidget(self.lbl_note)

        root.addLayout(mid, 1)

    # ---------------------------------------------------------------- 交互
    def _on_check(self, checked: bool):
        self.toggled.emit(self.task.id, checked)

    def _done_now(self) -> bool:
        """当前是否显示为"今天已完成"（hover 效果和色条都按它判断）。"""
        return is_done_for(self.task)

    def enterEvent(self, e):
        self._hover = True
        if not self._done_now():
            self.setObjectName("Card")
            self.setStyleSheet("")   # 触发 QSS 重算
            self.style().unpolish(self)
            self.style().polish(self)
        for b in (self.btn_snooze, self.btn_skip, self.btn_del):
            if not self._done_now():
                b.setVisible(True)
        self._shadow.setBlurRadius(22)
        self._shadow.setOffset(0, 4)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        for b in (self.btn_snooze, self.btn_skip, self.btn_del):
            b.setVisible(False)
        self._shadow.setBlurRadius(14)
        self._shadow.setOffset(0, 2)
        super().leaveEvent(e)

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.edit_requested.emit(self.task.id)
            e.accept()
            return
        super().mouseDoubleClickEvent(e)

    def mouseReleaseEvent(self, e):
        # 单击空白处 = 编辑；点在按钮/勾选框上不触发
        if e.button() == Qt.LeftButton:
            child = self.childAt(e.position().toPoint())
            if child in (None, self.lbl_title, self.lbl_meta, self.lbl_time):
                self.edit_requested.emit(self.task.id)
                e.accept()
                return
        super().mouseReleaseEvent(e)

    # ---------------------------------------------------------------- 绘制
    def paintEvent(self, e):
        super().paintEvent(e)
        # 左侧优先级色条：圆角矩形，贴在卡片左内侧（今天已完成的卡片不画）
        if self._done_now():
            return
        c = QColor(self.theme.c[self._bar_color_key])
        if not self._hover:
            c.setAlpha(200)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(c))
        h = min(self.height() - 26, 44)
        p.drawRoundedRect(QRectF(0.5, (self.height() - h) / 2, self._bar_w, h),
                          self._bar_w / 2, self._bar_w / 2)
        p.end()


# ---------------------------------------------------------------------------
# 空状态
# ---------------------------------------------------------------------------

class EmptyState(QWidget):
    def __init__(self, icon: str = "☕", text: str = "今天还没有安排", sub: str = "",
                 parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)
        lay.setSpacing(6)

        self.ico = QLabel(icon)
        self.ico.setObjectName("EmptyIcon")
        self.ico.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.ico)

        self.txt = QLabel(text)
        self.txt.setObjectName("EmptyText")
        self.txt.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.txt)

        if sub:
            s = QLabel(sub)
            s.setObjectName("Muted")
            s.setAlignment(Qt.AlignCenter)
            lay.addWidget(s)


# ---------------------------------------------------------------------------
# 小工具函数
# ---------------------------------------------------------------------------

def hline(color: str) -> QFrame:
    f = QFrame()
    f.setObjectName("Divider")
    f.setFixedHeight(1)
    f.setStyleSheet(f"background: {color};")
    return f


def chip(text: str, checked: bool = False, color: str | None = None) -> QPushButton:
    """带颜色的筛选胶囊。

    注意：一旦调用 setStyleSheet，全局 QSS 里 QPushButton#Chip 的规则就会被
    整体覆盖，所以这里必须把基础样式（圆角/内边距）一起写全，否则圆角会消失。
    """
    b = QPushButton(text)
    b.setObjectName("Chip")
    b.setCheckable(True)
    b.setChecked(checked)
    b.setCursor(Qt.PointingHandCursor)
    if color:
        b.setStyleSheet(
            "QPushButton#Chip {"
            f" border: 1px solid #E3F0F7; border-left: 6px solid {color};"
            " border-radius: 12px; padding: 3px 10px; font-size: 12px; }"
            "QPushButton#Chip:hover {"
            f" border: 1px solid #CDE7F1; border-left: 6px solid {color}; }}"
        )
    return b


def friendly_date(d: date) -> str:
    """今天 / 明天 / 昨天 / 3月14日 周五"""
    delta = (d - date.today()).days
    if delta == 0:
        base = "今天"
    elif delta == 1:
        base = "明天"
    elif delta == 2:
        base = "后天"
    elif delta == -1:
        base = "昨天"
    else:
        base = ""
    wd = cfg.WEEKDAY_CN[d.isoweekday()]
    txt = f"{d.month}月{d.day}日 {wd}"
    return f"{base} · {txt}" if base else txt
