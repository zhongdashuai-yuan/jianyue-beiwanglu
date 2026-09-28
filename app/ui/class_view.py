"""「班级」页：显示老师发下来的通告。

设计要点：
  * 通告是**只读**的 —— 同学只能看和标记已读，服务端才是权威
  * 未读有青色小圆点，点开即标记已读
  * 顶部显示同步状态：连得上会显示上次同步时间，连不上只显示"离线"，
    **不弹错误框**（老师电脑关机是常态，不该每次都打扰同学）
  * 老师发的任务会进「列表」页，这里只放通告
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from .. import config as cfg
from .widgets import EmptyState, soft_shadow


class AnnouncementCard(QFrame):
    """一条通告。点一下展开/收起正文，并标记已读。"""

    clicked = Signal(int)

    def __init__(self, ann: dict, theme, parent=None):
        super().__init__(parent)
        self.ann = ann
        self.theme = theme
        self._expanded = False
        unread = not ann.get("read_at")

        self.setObjectName("Card")
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        soft_shadow(self, theme.c["shadow"], blur=14, dy=2, alpha=70)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 11, 14, 11)
        lay.setSpacing(6)

        # 第一行：未读点 + 标题 + 时间
        row = QHBoxLayout()
        row.setSpacing(8)
        if unread:
            dot = QLabel("●")
            dot.setStyleSheet(f"color: {theme.c['accent']}; font-size: 10px;")
            row.addWidget(dot)
        title = QLabel(ann.get("title") or "(无标题)")
        title.setObjectName("CardTitle")
        title.setWordWrap(True)
        row.addWidget(title, 1)
        when = (ann.get("created_at") or "")[:16]
        lbl_time = QLabel(when)
        lbl_time.setObjectName("Muted")
        row.addWidget(lbl_time)
        lay.addLayout(row)

        # 第二行：作者
        meta = QLabel(f"来自 {ann.get('author') or '老师'}")
        meta.setObjectName("Muted")
        lay.addWidget(meta)

        # 正文：默认只显示一行摘要，点开看全文
        self.body = QLabel()
        self.body.setObjectName("Sub")
        self.body.setWordWrap(True)
        lay.addWidget(self.body)

        # hint 必须在 _refresh_body() 之前建好 —— 它内部会 setText/setVisible。
        # 之前这里顺序写反了，只要有一条带正文的通告，点开班级页就崩。
        self.hint = QLabel()
        self.hint.setObjectName("Muted")
        lay.addWidget(self.hint)

        self._refresh_body()

    def _refresh_body(self):
        text = (self.ann.get("body") or "").strip()
        if not text:
            self.body.setText("（这条通告没有正文）")
            self.hint.setVisible(False)
            return
        if self._expanded:
            self.body.setText(text)
            self.hint.setText("点击收起")
        else:
            first = text.splitlines()[0]
            self.body.setText(first[:60] + ("…" if len(first) > 60 else ""))
            self.hint.setText("点击展开全文" if len(text) > len(first) or
                              len(first) > 60 else "")

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._expanded = not self._expanded
            self._refresh_body()
            self.clicked.emit(int(self.ann.get("remote_id") or 0))
            e.accept()
            return
        super().mouseReleaseEvent(e)


class ClassView(QWidget):
    """班级通告页。"""

    def __init__(self, db, sync, theme, settings, parent=None):
        super().__init__(parent)
        self.db = db
        self.sync = sync
        self.theme = theme
        self.settings = settings

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        # ---- 顶部状态栏 ----
        bar = QFrame()
        bar.setObjectName("Panel")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(12, 8, 10, 8)
        bl.setSpacing(8)

        self.lbl_status = QLabel("")
        self.lbl_status.setObjectName("Sub")
        self.lbl_status.setWordWrap(True)
        bl.addWidget(self.lbl_status, 1)

        self.btn_sync = QPushButton("立即同步")
        self.btn_sync.setObjectName("Primary")
        self.btn_sync.setCursor(Qt.PointingHandCursor)
        self.btn_sync.clicked.connect(self._manual_sync)
        bl.addWidget(self.btn_sync)

        self.btn_setup = QPushButton("接入设置")
        self.btn_setup.setObjectName("Ghost")
        self.btn_setup.setCursor(Qt.PointingHandCursor)
        self.btn_setup.clicked.connect(self._open_setup)
        bl.addWidget(self.btn_setup)
        root.addWidget(bar)

        # ---- 通告列表 ----
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

        # 「接入设置」的信号由 MainWindow 接住去打开设置对话框
        self.on_open_setup = None

    # ------------------------------------------------------------------
    def _open_setup(self):
        if callable(self.on_open_setup):
            self.on_open_setup()

    def _manual_sync(self):
        res = self.sync.sync()
        self.refresh(res)
        # 结果由状态栏显示，不弹窗（联网失败是常态，不该打断同学）

    # ------------------------------------------------------------------
    def refresh(self, result=None):
        """重画通告列表与状态栏。result 是刚同步完的结果（可选）。"""
        c = self.theme.c
        st = result if result is not None else self.sync.last

        if not self.sync.enabled:
            self.lbl_status.setText("还没接入班级 —— 点右边「接入设置」，"
                                    "填老师给的服务器地址和接入密钥")
            self.btn_sync.setEnabled(False)
        else:
            self.btn_sync.setEnabled(True)
            if st is None:
                self.lbl_status.setText(f"已配置：{self.sync.server_url}")
            else:
                self.lbl_status.setText(st.summary())

        # 清空
        while self.vbox.count() > 1:
            item = self.vbox.takeAt(0)
            w = item.widget()
            if w:
                w.hide()
                w.setParent(None)
                w.deleteLater()

        anns = self.sync.announcements()
        if not anns:
            self.vbox.insertWidget(0, EmptyState(
                "📣",
                "还没有班级通告" if self.sync.enabled else "未接入班级",
                "老师发通告后，这里会自动出现"
                if self.sync.enabled else "点「接入设置」填入老师给的地址和密钥",
                parent=self.holder))
            return

        for a in anns:
            card = AnnouncementCard(a, self.theme, parent=self.holder)
            card.clicked.connect(self._on_card_clicked)
            self.vbox.insertWidget(self.vbox.count() - 1, card)

    def _on_card_clicked(self, remote_id: int):
        """点一下就标记已读（本地状态，不上传）。"""
        self.sync.mark_read(remote_id, True)
