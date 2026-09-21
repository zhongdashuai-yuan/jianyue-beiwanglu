"""新建 / 编辑任务对话框。

左侧竖排分区（内容 / 提醒 / 重复 / 分类标签），不用 Tab —— 一屏就能看到全部。
重复规则的 UI 会随规则类型动态切换参数区。
"""

from __future__ import annotations

from datetime import date, datetime, time

from PySide6.QtCore import QDate, QTime, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDateEdit, QDialog, QFrame,
                               QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QPushButton, QScrollArea, QSizePolicy,
                               QSpinBox, QStackedWidget, QTextEdit, QTimeEdit,
                               QVBoxLayout, QWidget)

from .. import config as cfg
from ..models import Task, now_str
from ..recurrence import describe


class RecurrenceEditor(QWidget):
    """重复规则编辑区：上面选类型，下面根据类型显示不同参数。"""

    changed = Signal()

    def __init__(self, task: Task, theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.task = task
        p = task.recur_params or {}

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        # 类型下拉
        row = QHBoxLayout()
        row.addWidget(QLabel("重复规则"))
        self.cmb = QComboBox()
        for key, label in cfg.RECUR_LABELS.items():
            self.cmb.addItem(label, key)
        idx = self.cmb.findData(task.recur)
        self.cmb.setCurrentIndex(max(0, idx))
        self.cmb.currentIndexChanged.connect(self._on_type_changed)
        row.addWidget(self.cmb, 1)
        lay.addLayout(row)

        # 参数区
        self.stack = QStackedWidget()
        lay.addWidget(self.stack)

        # --- 一次性：日期 ---
        w_once = QWidget()
        g = QHBoxLayout(w_once)
        g.setContentsMargins(0, 0, 0, 0)
        g.addWidget(QLabel("日期"))
        self.date_once = QDateEdit()
        self.date_once.setCalendarPopup(True)
        self.date_once.setDisplayFormat("yyyy-MM-dd")
        d = task.once_date or date.today().isoformat()
        self.date_once.setDate(QDate.fromString(d, "yyyy-MM-dd"))
        self.date_once.dateChanged.connect(self.changed)
        g.addWidget(self.date_once, 1)
        self.stack.addWidget(w_once)

        # --- 每天：无参数 ---
        w_daily = QWidget()
        QHBoxLayout(w_daily).addWidget(QLabel("每天同一时间提醒"))
        self.stack.addWidget(w_daily)

        # --- 每周几 ---
        w_weekly = QWidget()
        gv = QVBoxLayout(w_weekly)
        gv.setContentsMargins(0, 0, 0, 0)
        gv.addWidget(QLabel("选择星期（可多选）"))
        self.week_checks = []
        hw = QHBoxLayout()
        selected = set(p.get("weekdays") or [date.today().isoweekday()])
        for i in range(1, 8):
            cb = QPushButton(cfg.WEEKDAY_SHORT[i - 1])
            cb.setCheckable(True)
            cb.setObjectName("Chip")
            cb.setFixedWidth(38)
            cb.setChecked(i in selected)
            cb.setCursor(Qt.PointingHandCursor)
            cb.clicked.connect(self.changed)
            hw.addWidget(cb)
            self.week_checks.append(cb)
        hw.addStretch(1)
        gv.addLayout(hw)
        self.stack.addWidget(w_weekly)

        # --- 每月几号 ---
        w_monthly = QWidget()
        gv = QVBoxLayout(w_monthly)
        gv.setContentsMargins(0, 0, 0, 0)
        gv.addWidget(QLabel("每月哪几天（逗号分隔，如 1,15,30）"))
        self.edit_monthdays = QLineEdit(
            ",".join(str(x) for x in (p.get("monthdays") or [1])))
        self.edit_monthdays.textChanged.connect(self.changed)
        gv.addWidget(self.edit_monthdays)
        gv.addWidget(QLabel("提示：不到 31 号的月份，31 号会自动落到当月最后一天"))
        self.stack.addWidget(w_monthly)
        # --- 工作日：无参数 ---
        w_work = QWidget()
        gv = QVBoxLayout(w_work)
        gv.setContentsMargins(0, 0, 0, 0)
        gv.addWidget(QLabel("周一至周五提醒，自动跳过法定节假日，调休上班日照常提醒"))
        self.chk_defer = QCheckBox("遇到节假日顺延到下一个工作日")
        self.chk_defer.setChecked(bool(p.get("defer_on_holiday",
                                             cfg.DEFAULT_UI_SETTINGS["defer_on_holiday"])))
        self.chk_defer.stateChanged.connect(self.changed)
        gv.addWidget(self.chk_defer)
        self.stack.addWidget(w_work)

        # --- 单双周 ---
        w_bi = QWidget()
        gv = QVBoxLayout(w_bi)
        gv.setContentsMargins(0, 0, 0, 0)
        gv.addWidget(QLabel("每两周的哪一天"))
        hw = QHBoxLayout()
        self.bi_checks = []
        sel2 = set(p.get("weekdays") or [date.today().isoweekday()])
        for i in range(1, 8):
            cb = QPushButton(cfg.WEEKDAY_SHORT[i - 1])
            cb.setCheckable(True)
            cb.setObjectName("Chip")
            cb.setFixedWidth(38)
            cb.setChecked(i in sel2)
            cb.setCursor(Qt.PointingHandCursor)
            cb.clicked.connect(self.changed)
            hw.addWidget(cb)
            self.bi_checks.append(cb)
        hw.addStretch(1)
        gv.addLayout(hw)
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("哪一周"))
        self.cmb_parity = QComboBox()
        self.cmb_parity.addItem("单周（与基准周同奇偶）", "odd")
        self.cmb_parity.addItem("双周", "even")
        self.cmb_parity.setCurrentIndex(0 if p.get("parity", "odd") == "odd" else 1)
        self.cmb_parity.currentIndexChanged.connect(self.changed)
        row2.addWidget(self.cmb_parity, 1)
        gv.addLayout(row2)
        row3 = QHBoxLayout()
        row3.addWidget(QLabel("基准日"))
        self.date_ref = QDateEdit()
        self.date_ref.setCalendarPopup(True)
        self.date_ref.setDisplayFormat("yyyy-MM-dd")
        self.date_ref.setDate(QDate.fromString(
            p.get("ref_date") or cfg.DEFAULT_UI_SETTINGS["ref_biweek_date"], "yyyy-MM-dd"))
        self.date_ref.dateChanged.connect(self.changed)
        row3.addWidget(self.date_ref, 1)
        gv.addLayout(row3)
        gv.addWidget(QLabel("基准日所在的那一周算「单周」，之后每隔一周交替"))
        self.stack.addWidget(w_bi)

        # --- 每月最后一天 ---
        w_me = QWidget()
        QHBoxLayout(w_me).addWidget(QLabel("每月最后一天提醒"))
        self.stack.addWidget(w_me)

        # --- 每月倒数第 N 天 ---
        w_men = QWidget()
        g = QHBoxLayout(w_men)
        g.setContentsMargins(0, 0, 0, 0)
        g.addWidget(QLabel("每月倒数第"))
        self.spin_endn = QSpinBox()
        self.spin_endn.setRange(1, 15)
        self.spin_endn.setValue(int(p.get("n", 1)))
        self.spin_endn.valueChanged.connect(self.changed)
        g.addWidget(self.spin_endn)
        g.addWidget(QLabel("天"))
        g.addStretch(1)
        self.stack.addWidget(w_men)

        # --- 每月第几个周几 ---
        w_nth = QWidget()
        gv = QVBoxLayout(w_nth)
        gv.setContentsMargins(0, 0, 0, 0)
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("每月的第"))
        self.edit_occ = QLineEdit(
            ",".join(str(x) for x in (p.get("occurrences") or [1])))
        self.edit_occ.setPlaceholderText("1,3,-1  （-1 表示最后一个）")
        self.edit_occ.textChanged.connect(self.changed)
        r1.addWidget(self.edit_occ, 1)
        r1.addWidget(QLabel("个"))
        self.cmb_wd = QComboBox()
        for i in range(1, 8):
            self.cmb_wd.addItem(cfg.WEEKDAY_CN[i], i)
        self.cmb_wd.setCurrentIndex(int(p.get("weekday", 1)) - 1)
        self.cmb_wd.currentIndexChanged.connect(self.changed)
        r1.addWidget(self.cmb_wd)
        gv.addLayout(r1)
        gv.addWidget(QLabel("例：1,3 → 第一个和第三个周三；-1 → 最后一个周三"))
        self.stack.addWidget(w_nth)

        # --- 每季度 ---
        w_q = QWidget()
        gv = QVBoxLayout(w_q)
        gv.setContentsMargins(0, 0, 0, 0)
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("季度"))
        self.cmb_qmode = QComboBox()
        self.cmb_qmode.addItem("最后一天", "last_day")
        self.cmb_qmode.addItem("第一天", "first_day")
        self.cmb_qmode.addItem("第 N 天", "nth_day")
        self.cmb_qmode.addItem("第 N 个工作日", "nth_workday")
        modes = [self.cmb_qmode.itemData(i) for i in range(self.cmb_qmode.count())]
        self.cmb_qmode.setCurrentIndex(max(0, modes.index(p.get("mode", "last_day"))
                                           if p.get("mode", "last_day") in modes else 0))
        self.cmb_qmode.currentIndexChanged.connect(self.changed)
        r1.addWidget(self.cmb_qmode, 1)
        r1.addWidget(QLabel("N ="))
        self.spin_qn = QSpinBox()
        self.spin_qn.setRange(1, 92)
        self.spin_qn.setValue(int(p.get("n", 1)))
        self.spin_qn.valueChanged.connect(self.changed)
        r1.addWidget(self.spin_qn)
        gv.addLayout(r1)
        gv.addWidget(QLabel("季度以 1/4/7/10 月为界；「第 N 个工作日」会自动跳过节假日"))
        self.stack.addWidget(w_q)

        # 页面顺序必须和 cfg 里类型顺序一致
        self._order = [cfg.RECUR_NONE, cfg.RECUR_DAILY, cfg.RECUR_WEEKLY,
                       cfg.RECUR_MONTHLY, cfg.RECUR_WORKDAY, cfg.RECUR_BIWEEK,
                       cfg.RECUR_MONTH_END, cfg.RECUR_MONTH_END_N,
                       cfg.RECUR_MONTH_NTH, cfg.RECUR_QUARTER]
        self._sync_stack()

    def _on_type_changed(self, _):
        self._sync_stack()
        self.changed.emit()

    def _sync_stack(self):
        key = self.cmb.currentData()
        try:
            self.stack.setCurrentIndex(self._order.index(key))
        except ValueError:
            self.stack.setCurrentIndex(1)

    # ------------------------------------------------------------------
    def collect(self) -> tuple[str, dict, str | None]:
        """返回 (recur, recur_params, once_date)"""
        key = self.cmb.currentData()
        p: dict = {}
        if key == cfg.RECUR_NONE:
            return key, p, self.date_once.date().toString("yyyy-MM-dd")
        if key == cfg.RECUR_WEEKLY:
            wd = [i + 1 for i, cb in enumerate(self.week_checks) if cb.isChecked()]
            p["weekdays"] = wd or [date.today().isoweekday()]
        elif key == cfg.RECUR_MONTHLY:
            days = []
            for part in self.edit_monthdays.text().replace("，", ",").split(","):
                part = part.strip()
                if part.isdigit() and 1 <= int(part) <= 31:
                    days.append(int(part))
            p["monthdays"] = sorted(set(days)) or [1]
        elif key == cfg.RECUR_WORKDAY:
            p["defer_on_holiday"] = self.chk_defer.isChecked()
        elif key == cfg.RECUR_BIWEEK:
            wd = [i + 1 for i, cb in enumerate(self.bi_checks) if cb.isChecked()]
            p["weekdays"] = wd or [date.today().isoweekday()]
            p["parity"] = self.cmb_parity.currentData()
            p["ref_date"] = self.date_ref.date().toString("yyyy-MM-dd")
        elif key == cfg.RECUR_MONTH_END_N:
            p["n"] = self.spin_endn.value()
        elif key == cfg.RECUR_MONTH_NTH:
            occ = []
            for part in self.edit_occ.text().replace("，", ",").split(","):
                part = part.strip()
                if part.lstrip("-").isdigit():
                    v = int(part)
                    if v == -1 or 1 <= v <= 5:
                        occ.append(v)
            p["occurrences"] = occ or [1]
            p["weekday"] = self.cmb_wd.currentData()
        elif key == cfg.RECUR_QUARTER:
            p["mode"] = self.cmb_qmode.currentData()
            p["n"] = self.spin_qn.value()
        # 其它规则也支持"遇假顺延"
        if key not in (cfg.RECUR_WORKDAY, cfg.RECUR_NONE):
            p["defer_on_holiday"] = cfg.DEFAULT_UI_SETTINGS["defer_on_holiday"]
        return key, p, None


class TaskEditDialog(QDialog):
    """新建 / 编辑一个任务。"""

    def __init__(self, theme, task: Task | None = None, db=None, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.db = db
        self.is_new = task is None
        self.task = task.copy() if task else Task(
            title="", priority=cfg.DEFAULT_UI_SETTINGS.get("default_priority", "中"),
            category=cfg.DEFAULT_UI_SETTINGS.get("default_category", "工作"),
            recur=cfg.RECUR_DAILY, times=["09:00"], created_at=now_str())

        self.setWindowTitle("新建提醒" if self.is_new else "编辑提醒")
        from ..theme import make_icon
        self.setWindowIcon(make_icon(64, theme.c))
        self.setMinimumWidth(560)
        self.setMinimumHeight(620)
        self.setObjectName("RootBackground")
        self.setStyleSheet(f"QDialog#RootBackground {{ background: {theme.c['bg_grad_a']}; }}")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 16)
        outer.setSpacing(12)

        # ---------------- 标题 ----------------
        head = QLabel("新建提醒" if self.is_new else "编辑提醒")
        head.setObjectName("H1")
        outer.addWidget(head)
        sub = QLabel("带 * 的是必填；重复规则决定它以后哪些天会出现")
        sub.setObjectName("Sub")
        outer.addWidget(sub)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        bl = QVBoxLayout(body)
        bl.setContentsMargins(0, 0, 8, 0)
        bl.setSpacing(14)

        # ---------------- 内容 ----------------
        bl.addWidget(self._section("内容", theme))
        self.edit_title = QLineEdit(self.task.title)
        self.edit_title.setPlaceholderText("要做什么？例如：交周报")
        self.edit_title.setMinimumHeight(36)
        bl.addWidget(self.edit_title)

        self.edit_note = QTextEdit(self.task.note)
        self.edit_note.setPlaceholderText("备注（可多行，可以贴链接）")
        self.edit_note.setFixedHeight(70)
        bl.addWidget(self.edit_note)

        row = QHBoxLayout()
        row.setSpacing(10)
        # 优先级
        pv = QVBoxLayout()
        pv.addWidget(QLabel("优先级"))
        self.cmb_prio = QComboBox()
        self.cmb_prio.addItems(cfg.PRIORITIES)
        self.cmb_prio.setCurrentText(self.task.priority)
        pv.addWidget(self.cmb_prio)
        row.addLayout(pv, 1)
        # 分类
        cv = QVBoxLayout()
        cv.addWidget(QLabel("分类"))
        self.cmb_cat = QComboBox()
        self.cmb_cat.setEditable(True)
        cats = [c["name"] for c in (db.categories() if db else
                                    [{"name": x["name"]} for x in cfg.DEFAULT_CATEGORIES])]
        self.cmb_cat.addItems(cats)
        self.cmb_cat.setCurrentText(self.task.category)
        cv.addWidget(self.cmb_cat)
        row.addLayout(cv, 1)
        bl.addLayout(row)

        # 标签
        tv = QVBoxLayout()
        tv.addWidget(QLabel("标签（逗号分隔）"))
        self.edit_tags = QLineEdit("，".join(self.task.tags))
        self.edit_tags.setPlaceholderText("例如：重要，紧急")
        tv.addWidget(self.edit_tags)
        bl.addLayout(tv)

        bl.addWidget(self._divider(theme))

        # ---------------- 提醒时间 ----------------
        bl.addWidget(self._section("提醒时间", theme))
        trow = QHBoxLayout()
        trow.setSpacing(8)
        self.time_edits: list[QTimeEdit] = []
        times = self.task.times or ["09:00"]
        for i, ts in enumerate(times):
            trow.addWidget(self._time_edit(ts))
        trow.addStretch(1)
        self.btn_add_time = QPushButton("＋ 加一个时间点")
        self.btn_add_time.setObjectName("Ghost")
        self.btn_add_time.clicked.connect(lambda: self._add_time("12:00"))
        trow.addWidget(self.btn_add_time)
        bl.addLayout(trow)
        hint = QLabel("第一个时间是主时间；后面加的时间点会在同一天依次再提醒一次")
        hint.setObjectName("Muted")
        bl.addWidget(hint)

        lrow = QHBoxLayout()
        lrow.addWidget(QLabel("提前提醒"))
        self.cmb_lead = QComboBox()
        for m in cfg.LEAD_CHOICES:
            self.cmb_lead.addItem("准点提醒" if m == 0 else f"提前 {m} 分钟", m)
        i = self.cmb_lead.findData(int(self.task.lead_minutes or 0))
        self.cmb_lead.setCurrentIndex(max(0, i))
        lrow.addWidget(self.cmb_lead)
        lrow.addStretch(1)
        self.chk_enabled = QCheckBox("启用这个提醒")
        self.chk_enabled.setChecked(self.task.enabled)
        lrow.addWidget(self.chk_enabled)
        bl.addLayout(lrow)

        bl.addWidget(self._divider(theme))

        # ---------------- 重复规则 ----------------
        bl.addWidget(self._section("重复", theme))
        self.recur_editor = RecurrenceEditor(self.task, theme)
        self.recur_editor.changed.connect(self._update_preview)
        bl.addWidget(self.recur_editor)

        self.lbl_preview = QLabel()
        self.lbl_preview.setObjectName("Sub")
        self.lbl_preview.setWordWrap(True)
        bl.addWidget(self.lbl_preview)

        bl.addStretch(1)

        # ---------------- 底部按钮 ----------------
        outer.addWidget(self._divider(theme))
        btns = QHBoxLayout()
        self.lbl_next = QLabel()
        self.lbl_next.setObjectName("Muted")
        btns.addWidget(self.lbl_next, 1)

        if not self.is_new:
            btn_del = QPushButton("删除")
            btn_del.setObjectName("Danger")
            btn_del.clicked.connect(self._delete)
            btns.addWidget(btn_del)

        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(self.reject)
        btns.addWidget(btn_cancel)

        btn_ok = QPushButton("保存")
        btn_ok.setObjectName("Primary")
        btn_ok.setDefault(True)
        btn_ok.clicked.connect(self._save)
        btns.addWidget(btn_ok)
        outer.addLayout(btns)

        self.edit_title.setFocus()
        self.edit_title.selectAll()
        self._update_preview()

    # ------------------------------------------------------------------ 小零件
    def _section(self, text: str, theme) -> QLabel:
        lb = QLabel(text)
        lb.setStyleSheet(
            f"color: {theme.c['accent_press']}; font-size: 13px; font-weight: 600;")
        return lb

    def _divider(self, theme) -> QFrame:
        f = QFrame()
        f.setFixedHeight(1)
        f.setStyleSheet(f"background: {theme.c['divider']};")
        return f

    def _time_edit(self, value: str) -> QTimeEdit:
        te = QTimeEdit()
        te.setDisplayFormat("HH:mm")
        te.setTime(QTime.fromString(value, "HH:mm"))
        te.setFixedWidth(96)
        te.timeChanged.connect(self._update_preview)
        self.time_edits.append(te)
        return te

    def _add_time(self, value: str):
        row = self.time_edits[-1].parentWidget().layout()
        te = self._time_edit(value)
        # 插到"加一个时间点"按钮之前
        idx = row.indexOf(self.btn_add_time)
        row.insertWidget(idx, te)
        # 给每个时间点配一个删除按钮（点已添加的）
        self._update_preview()

    def _collect_times(self) -> list[str]:
        out = []
        for te in self.time_edits:
            s = te.time().toString("HH:mm")
            if s not in out:
                out.append(s)
        return sorted(out) or ["09:00"]

    # ------------------------------------------------------------------
    def _update_preview(self):
        recur, params, once = self.recur_editor.collect()
        tmp = self.task.copy()
        tmp.recur = recur
        tmp.recur_params = params
        tmp.once_date = once
        tmp.enabled = True
        try:
            from ..recurrence import next_occurrence
            res = next_occurrence(tmp, after=date.today())
            if res:
                actual, planned = res
                txt = f"下一次：{actual.month}月{actual.day}日（{cfg.WEEKDAY_CN[actual.isoweekday()]}）"
                if planned:
                    txt += f"  ⚠ 原计划 {planned.month}月{planned.day}日，因假期顺延"
                self.lbl_next.setText(txt)
            else:
                self.lbl_next.setText("按当前规则，未来一年内没有提醒")
        except Exception as e:
            self.lbl_next.setText(f"规则有问题：{e}")
        self.lbl_preview.setText(f"规则：{describe(tmp)}")

    def _save(self):
        title = self.edit_title.text().strip()
        if not title:
            QMessageBox.information(self, "还差一点", "请填写要提醒的事情（标题）。")
            self.edit_title.setFocus()
            return
        t = self.task
        t.title = title
        t.note = self.edit_note.toPlainText().strip()
        t.priority = self.cmb_prio.currentText()
        t.category = self.cmb_cat.currentText().strip() or "未分类"
        t.tags = [x.strip() for x in self.edit_tags.text().replace("，", ",").split(",")
                  if x.strip()]
        t.times = self._collect_times()
        t.lead_minutes = int(self.cmb_lead.currentData() or 0)
        t.enabled = self.chk_enabled.isChecked()
        recur, params, once = self.recur_editor.collect()
        t.recur = recur
        t.recur_params = params
        t.once_date = once
        # 规则变了，之前的"跳过"记录作废
        t.skip_dates = []
        self.accept()

    def _delete(self):
        if QMessageBox.question(self, "删除", f"确定删除「{self.task.title}」吗？删除后无法恢复。",
                                QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            self.done(2)     # 2 = 删除

    def result_task(self) -> Task:
        return self.task
