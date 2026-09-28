"""「管理」页：老师在自己电脑上发通告、发班级任务、撤回、看接入名单。

设计要点：
  * **只有填了管理员密钥才会出现**（见 MainWindow 的侧边栏逻辑）。
    同学那边看不到这个页面，避免误操作。
  * 全部走 HTTP 接口，**不直接读写服务端数据库** —— 用的就是 `send.py` 那套接口，
    保证"命令行能发的，界面上也能发"，不会出现两套逻辑不一致。
  * 网络失败只显示一行提示，**不弹错误框**：老师电脑和服务端可能不在同一台机器上，
    连不上是常态，不该每次刷新都糊一脸弹窗。
  * 撤回不是删除：服务端只把 `withdrawn` 置 1，同学下次同步时本地才消失。
    所以这里撤回后条目仍然列出来（灰掉、标"已撤回"），老师能看出自己撤过什么。
"""

from __future__ import annotations

from PySide6.QtCore import QDate, Qt, QTime, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDateEdit, QFrame,
                               QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
                               QPushButton, QScrollArea, QTabWidget, QTimeEdit,
                               QVBoxLayout, QWidget)

from .. import config as cfg
from ..admin_api import AdminApi

WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

# 管理页只暴露最常用的几种重复方式：老师发班级任务大多是"只一次 / 每天 /
# 每周几 / 每个工作日"。剩下的复杂规则（单双周、倒数第 N 天那些）留给同学们
# 自己在本机建提醒用，免得这个页面变成一张巨大的表单。
TASK_RECUR_CHOICES = [
    (cfg.RECUR_NONE, "只提醒一次"),
    (cfg.RECUR_DAILY, "每天"),
    (cfg.RECUR_WEEKLY, "每周（选星期几）"),
    (cfg.RECUR_WORKDAY, "每个工作日"),
    (cfg.RECUR_MONTHLY, "每月几号"),
]


def _section(title: str) -> QLabel:
    lbl = QLabel(title)
    lbl.setObjectName("CardTitle")
    return lbl


def _muted(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("Muted")
    lbl.setWordWrap(True)
    return lbl


class _Row(QFrame):
    """列表里的一行：标题 + 说明 + 一个撤回按钮。"""

    withdraw = Signal(int)

    def __init__(self, item_id: int, title: str, meta: str, body: str = "",
                 withdrawn: bool = False, parent=None):
        super().__init__(parent)
        self.item_id = int(item_id)
        self.setObjectName("Card")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 9, 12, 9)
        lay.setSpacing(10)

        left = QVBoxLayout()
        left.setSpacing(3)

        head = QLabel(title or "(无标题)")
        head.setObjectName("CardTitle")
        head.setWordWrap(True)
        if withdrawn:
            # 撤回过的灰掉，一眼能看出这条已经不在同学那边了
            head.setStyleSheet("color: #9aa7b4; text-decoration: line-through;")
        left.addWidget(head)

        if body:
            snippet = body.strip().splitlines()[0][:50]
            left.addWidget(_muted(snippet))

        left.addWidget(_muted(f"{meta}　·　ID {self.item_id}"
                             + ("　·　已撤回" if withdrawn else "")))
        lay.addLayout(left, 1)

        self.btn = QPushButton("已撤回" if withdrawn else "撤回")
        self.btn.setObjectName("Ghost")
        self.btn.setCursor(Qt.PointingHandCursor)
        self.btn.setEnabled(not withdrawn)
        self.btn.clicked.connect(lambda: self.withdraw.emit(self.item_id))
        lay.addWidget(self.btn, 0, Qt.AlignTop)


class AdminView(QWidget):
    """老师端管理页。"""

    def __init__(self, settings, theme, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.theme = theme
        self.api = AdminApi(settings)
        self._last_error = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        # ---------------- 顶部状态栏 ----------------
        bar = QFrame()
        bar.setObjectName("Panel")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(12, 8, 10, 8)
        bl.setSpacing(8)

        self.lbl_status = QLabel("")
        self.lbl_status.setObjectName("Sub")
        self.lbl_status.setWordWrap(True)
        bl.addWidget(self.lbl_status, 1)

        self.btn_refresh = QPushButton("刷新")
        self.btn_refresh.setObjectName("Primary")
        self.btn_refresh.setCursor(Qt.PointingHandCursor)
        self.btn_refresh.clicked.connect(self.refresh)
        bl.addWidget(self.btn_refresh)
        root.addWidget(bar)

        # ---------------- 三个标签页 ----------------
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_publish_tab(), "发通告 / 发任务")
        self.tabs.addTab(self._build_content_tab(), "已发布")
        self.tabs.addTab(self._build_members_tab(), "接入名单")
        root.addWidget(self.tabs, 1)

    # ------------------------------------------------------------------ 构建
    def _build_publish_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(12)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        holder = QWidget()
        lay = QVBoxLayout(holder)
        lay.setContentsMargins(2, 2, 8, 8)
        lay.setSpacing(8)

        # ---- 发通告 ----
        lay.addWidget(_section("发通告"))
        lay.addWidget(_muted("通告是只读的，同学只能看和标记已读，不会进他们的待办列表。"))

        self.edit_ann_title = QLineEdit()
        self.edit_ann_title.setPlaceholderText("标题，例如：明天交第 3 章作业")
        lay.addWidget(self.edit_ann_title)

        self.edit_ann_body = QPlainTextEdit()
        self.edit_ann_body.setPlaceholderText("正文（可留空）")
        self.edit_ann_body.setFixedHeight(90)
        lay.addWidget(self.edit_ann_body)

        arow = QHBoxLayout()
        self.btn_send_ann = QPushButton("发给全班")
        self.btn_send_ann.setObjectName("Primary")
        self.btn_send_ann.setCursor(Qt.PointingHandCursor)
        self.btn_send_ann.clicked.connect(self._send_announcement)
        arow.addWidget(self.btn_send_ann)
        self.lbl_ann_msg = _muted("")
        arow.addWidget(self.lbl_ann_msg, 1)
        lay.addLayout(arow)

        # ---- 发任务 ----
        lay.addWidget(_section("发班级任务"))
        lay.addWidget(_muted("班级任务会进同学的待办列表，标着「老师」；"
                             "同学删不掉，只有你在能这里撤回。"))

        self.edit_task_title = QLineEdit()
        self.edit_task_title.setPlaceholderText("任务标题，例如：交读书笔记")
        lay.addWidget(self.edit_task_title)

        self.edit_task_note = QLineEdit()
        self.edit_task_note.setPlaceholderText("备注（可留空）")
        lay.addWidget(self.edit_task_note)

        when = QHBoxLayout()
        when.setSpacing(6)
        when.addWidget(QLabel("日期"))
        self.date_task = QDateEdit()
        self.date_task.setCalendarPopup(True)
        self.date_task.setDisplayFormat("yyyy-MM-dd")
        self.date_task.setDate(QDate.currentDate())
        # 宽度必须显式给足：全局 QSS 给 QDateEdit/QTimeEdit 的默认宽度是按英文
        # 排的，装不下 "2026-09-28"/"19:00"，真机上会被截成 "2026-09-"/"19:0"。
        # 注意 sizeHint() 是**不含 QSS padding** 的（QSS 用内容盒模型），
        # theme.py 里给了 `padding: 6px 10px`，所以左右还要各加 10px，
        # 否则算着够、实际还是被截。这个坑真机截图才看得出来。
        _INPUT_PAD = 20
        self.date_task.setFixedWidth(self.date_task.sizeHint().width() + _INPUT_PAD)
        when.addWidget(self.date_task)

        when.addWidget(QLabel("时间"))
        self.time_task = QTimeEdit()
        self.time_task.setDisplayFormat("HH:mm")
        self.time_task.setTime(QTime(19, 0))
        self.time_task.setFixedWidth(self.time_task.sizeHint().width() + _INPUT_PAD)
        when.addWidget(self.time_task)
        when.addStretch(1)
        lay.addLayout(when)

        rrow = QHBoxLayout()
        rrow.setSpacing(6)
        rrow.addWidget(QLabel("重复"))
        self.cmb_recur = QComboBox()
        for key, label in TASK_RECUR_CHOICES:
            self.cmb_recur.addItem(label, key)
        self.cmb_recur.currentIndexChanged.connect(self._on_recur_changed)
        rrow.addWidget(self.cmb_recur, 1)
        lay.addLayout(rrow)

        # 只有选「每周」时才需要挑星期几
        self.week_box = QWidget()
        wl = QHBoxLayout(self.week_box)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(4)
        wl.addWidget(QLabel("星期"))
        self.chk_days: list[QCheckBox] = []
        for i, name in enumerate(WEEKDAY_NAMES):
            c = QCheckBox(name)
            c.setChecked(i < 5)          # 默认周一到周五，老师最常用
            self.chk_days.append(c)
            wl.addWidget(c)
        wl.addStretch(1)
        lay.addWidget(self.week_box)
        self.week_box.setVisible(False)

        trow = QHBoxLayout()
        self.btn_send_task = QPushButton("发布任务")
        self.btn_send_task.setObjectName("Primary")
        self.btn_send_task.setCursor(Qt.PointingHandCursor)
        self.btn_send_task.clicked.connect(self._send_task)
        trow.addWidget(self.btn_send_task)
        self.lbl_task_msg = _muted("")
        trow.addWidget(self.lbl_task_msg, 1)
        lay.addLayout(trow)

        lay.addStretch(1)
        scroll.setWidget(holder)
        outer.addWidget(scroll)
        return page

    def _build_content_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)

        outer.addWidget(_section("已发布的通告"))
        self.ann_holder = QWidget()
        self.ann_box = QVBoxLayout(self.ann_holder)
        self.ann_box.setContentsMargins(0, 0, 0, 0)
        self.ann_box.setSpacing(6)
        outer.addWidget(self.ann_holder)

        outer.addWidget(_section("已发布的班级任务"))
        self.task_holder = QWidget()
        self.task_box = QVBoxLayout(self.task_holder)
        self.task_box.setContentsMargins(0, 0, 0, 0)
        self.task_box.setSpacing(6)
        outer.addWidget(self.task_holder)
        outer.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(page)
        wrap = QWidget()
        wl = QVBoxLayout(wrap)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.addWidget(scroll)
        return wrap

    def _build_members_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)
        outer.addWidget(_section("谁接入了"))
        outer.addWidget(_muted("同学第一次同步时就会登记在这里。"
                               "填了名字的会显示名字，没填的只显示设备号。"))
        self.member_holder = QWidget()
        self.member_box = QVBoxLayout(self.member_holder)
        self.member_box.setContentsMargins(0, 0, 0, 0)
        self.member_box.setSpacing(6)
        outer.addWidget(self.member_holder)
        outer.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(page)
        wrap = QWidget()
        wl = QVBoxLayout(wrap)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.addWidget(scroll)
        return wrap

    # ------------------------------------------------------------------ 交互
    def _on_recur_changed(self):
        key = self.cmb_recur.currentData()
        self.week_box.setVisible(key == cfg.RECUR_WEEKLY)

    def _clear_box(self, box: QVBoxLayout):
        """清空一个列表容器。

        必须先 hide() 再 setParent(None) —— 直接 setParent(None) 会让控件
        短暂变成独立顶层窗口，屏幕上闪一片白框。项目里踩过这个坑。
        """
        while box.count():
            item = box.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()

    def _fill(self, box: QVBoxLayout, rows: list[QWidget], empty_text: str):
        self._clear_box(box)
        if not rows:
            box.addWidget(_muted(empty_text))
            return
        for r in rows:
            box.addWidget(r)

    # ------------------------------------------------------------------ 发送
    def _send_announcement(self):
        res = self.api.publish_announcement(self.edit_ann_title.text(),
                                           self.edit_ann_body.toPlainText())
        if res.ok:
            self.lbl_ann_msg.setText(f"已发给全班（ID {res.data.get('id')}）")
            self.edit_ann_title.clear()
            self.edit_ann_body.clear()
            self.refresh()
        else:
            self.lbl_ann_msg.setText(f"发送失败：{res.error}")

    def _build_recur_params(self) -> tuple[str, dict] | str:
        """把界面上的选择翻译成服务端要的 recur + recur_params。

        返回 (recur, params)，或者返回一个字符串表示出错（给老师看的提示）。
        """
        key = self.cmb_recur.currentData()
        d = self.date_task.date()
        once_date = f"{d.year():04d}-{d.month():02d}-{d.day():02d}"

        if key == cfg.RECUR_NONE:
            return key, {}
        if key == cfg.RECUR_WEEKLY:
            days = [i + 1 for i, c in enumerate(self.chk_days) if c.isChecked()]
            if not days:
                return "选了「每周」但一天都没勾，请勾至少一个星期几"
            return key, {"weekdays": days, "defer_on_holiday": True}
        if key == cfg.RECUR_MONTHLY:
            # 用所选日期的"日"作为每月几号，老师不用再找输入框
            return key, {"monthdays": [d.day()], "defer_on_holiday": True}
        return key, {"defer_on_holiday": True}

    def _send_task(self):
        built = self._build_recur_params()
        if isinstance(built, str):
            self.lbl_task_msg.setText(built)
            return
        recur, params = built
        t = self.time_task.time()
        hhmm = f"{t.hour():02d}:{t.minute():02d}"
        d = self.date_task.date()
        once_date = f"{d.year():04d}-{d.month():02d}-{d.day():02d}"

        # 重复任务不该带 once_date（那是"只提醒一次"专用的），否则同学那边
        # 首次计算会把日期算错。一次性任务才需要它。
        res = self.api.publish_task(
            title=self.edit_task_title.text(),
            note=self.edit_task_note.text(),
            times=[hhmm], recur=recur, recur_params=params,
            once_date=once_date if recur == cfg.RECUR_NONE else None)
        if res.ok:
            self.lbl_task_msg.setText(f"已发布（ID {res.data.get('id')}）")
            self.edit_task_title.clear()
            self.edit_task_note.clear()
            self.refresh()
        else:
            self.lbl_task_msg.setText(f"发布失败：{res.error}")

    def _withdraw_ann(self, ann_id: int):
        res = self.api.withdraw_announcement(ann_id)
        self.refresh()
        if not res.ok:
            self.lbl_status.setText(f"撤回失败：{res.error}")

    def _withdraw_task(self, task_id: int):
        res = self.api.withdraw_task(task_id)
        self.refresh()
        if not res.ok:
            self.lbl_status.setText(f"撤回失败：{res.error}")

    # ------------------------------------------------------------------ 刷新
    def refresh(self):
        """从服务端拉一次全量，重画三个标签页。"""
        if not self.api.configured:
            self.lbl_status.setText("还没配置管理权限：去「设置 → 班级通告」填上"
                                    "服务器地址和管理员密钥（管理员密钥不要发给同学）")
            self._fill(self.ann_box, [], "未连接")
            self._fill(self.task_box, [], "未连接")
            self._fill(self.member_box, [], "未连接")
            return

        res = self.api.fetch_all()
        if not res.ok:
            self.lbl_status.setText(f"连不上服务端：{res.error}　"
                                    f"（服务端地址 {self.api.server_url}）")
            return

        stats = res.data.get("stats") or {}
        self.lbl_status.setText(
            f"已连接 {self.api.server_url}　·　"
            f"通告 {stats.get('announcements', 0)} 条　·　"
            f"班级任务 {stats.get('tasks', 0)} 个　·　"
            f"接入 {stats.get('members', 0)} 人　·　更新于 {res.at}")

        anns = res.data.get("announcements") or []
        rows = []
        for a in anns:
            rows.append(_Row(a.get("id"), a.get("title"),
                             (a.get("created_at") or "")[:16],
                             a.get("body") or "",
                             bool(a.get("withdrawn"))))
            rows[-1].withdraw.connect(self._withdraw_ann)
        self._fill(self.ann_box, rows, "还没有发过通告")

        tasks = res.data.get("tasks") or []
        trows = []
        for t in tasks:
            times = t.get("times") or []
            time_txt = "、".join(times) if isinstance(times, list) else str(times)
            recur = cfg.RECUR_LABELS.get(t.get("recur") or "none", t.get("recur") or "")
            trows.append(_Row(t.get("id"), t.get("title"),
                              f"{recur}　{time_txt}　{(t.get('created_at') or '')[:16]}",
                              t.get("note") or "",
                              bool(t.get("withdrawn"))))
            trows[-1].withdraw.connect(self._withdraw_task)
        self._fill(self.task_box, trows, "还没有发过班级任务")

        members = res.data.get("members") or []
        mrows = []
        for m in members:
            name = (m.get("name") or "").strip() or "（没填名字）"
            seen = (m.get("last_seen") or "")[:16]
            first = (m.get("first_seen") or "")[:16]
            rows_w = QFrame()
            rows_w.setObjectName("Card")
            rl = QVBoxLayout(rows_w)
            rl.setContentsMargins(12, 9, 12, 9)
            rl.setSpacing(3)
            head = QLabel(name)
            head.setObjectName("CardTitle")
            rl.addWidget(head)
            rl.addWidget(_muted(f"设备 {m.get('device_id') or '?'}"))
            rl.addWidget(_muted(f"首次接入 {first}　·　最后同步 {seen}"))
            mrows.append(rows_w)
        self._fill(self.member_box, mrows, "还没有同学接入")
