"""冒烟测试：不需要真人点，跑一遍就能发现大部分低级错误。

用法（在 PyCharm 的 Terminal 里）：
    python tools/smoke_test.py

它做这些事：
  1. 用临时数据库建表
  2. 造几条覆盖各种重复规则的任务
  3. 检查重复引擎算出来的日期对不对（含节假日顺延）
  4. 勾选完成 -> 检查统计
  5. 用 offscreen 模式把主窗口、弹窗、对话框真的构造一遍（不显示）
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PASS, FAIL = [], []

# 控制台默认是 GBK，中文会乱码，所以同时写一份 UTF-8 报告到文件
REPORT = Path(__file__).resolve().parent.parent / "smoke_report.txt"
_report_lines: list[str] = []


def say(text: str):
    _report_lines.append(text)
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("utf-8", "replace").decode("ascii", "replace"))


def check(name: str, cond: bool, extra: str = ""):
    (PASS if cond else FAIL).append(name)
    say(("  [OK]   " if cond else "  [FAIL] ") + name + (f"   {extra}" if extra else ""))


def step(name: str, fn):
    """跑一个会抛异常的步骤，异常也算测试失败（而不是让脚本直接中断）。

    之前就是这里吃过亏：界面里一处 AttributeError 把脚本打断了，
    结果报告只显示 64 项通过、0 项失败，看不出问题。
    """
    try:
        fn()
        check(name, True)
    except Exception as e:
        import traceback
        check(name, False, f"{type(e).__name__}: {e}")
        say("        " + traceback.format_exc().replace("\n", "\n        ")[:900])


def main() -> int:
    from app import config as cfg
    from app.database import Database
    from app.models import Task
    from app.recurrence import (compute_next_at, describe, holds_on, next_occurrence,
                               occurs_on)
    from app.holidays import CALENDAR, reload_calendar
    from app import stats as stats_mod

    tmp = Path(tempfile.mkdtemp(prefix="memo_test_"))
    db = Database(tmp / "test.db")

    say("\n=== 1. 数据库 ===")
    check("建表成功", db.conn is not None)
    check("默认分类存在", len(db.categories()) >= 4, str([c["name"] for c in db.categories()]))

    say("\n=== 2. 重复规则引擎 ===")
    base = dict(title="测试", times=["09:00"], enabled=True)

    t_daily = Task(**base, recur=cfg.RECUR_DAILY)
    check("每天：今天命中", occurs_on(t_daily, date.today()))

    t_weekly = Task(**base, recur=cfg.RECUR_WEEKLY,
                    recur_params={"weekdays": [1, 3]})   # 周一、周三
    mon = date(2026, 3, 2)      # 2026-03-02 是周一
    wed = date(2026, 3, 4)
    tue = date(2026, 3, 3)
    check("每周一、三：周一命中", occurs_on(t_weekly, mon))
    check("每周一、三：周三命中", occurs_on(t_weekly, wed))
    check("每周一、三：周二不命中", not occurs_on(t_weekly, tue))

    t_month = Task(**base, recur=cfg.RECUR_MONTHLY, recur_params={"monthdays": [1, 15]})
    check("每月1、15号：1号命中", occurs_on(t_month, date(2026, 3, 1)))
    check("每月1、15号：15号命中", occurs_on(t_month, date(2026, 3, 15)))
    check("每月1、15号：16号不命中", not occurs_on(t_month, date(2026, 3, 16)))

    t_end = Task(**base, recur=cfg.RECUR_MONTH_END)
    check("每月最后一天：2月28日命中(2026非闰年)", occurs_on(t_end, date(2026, 2, 28)))
    check("每月最后一天：2月27日不命中", not occurs_on(t_end, date(2026, 2, 27)))

    t_endn = Task(**base, recur=cfg.RECUR_MONTH_END_N, recur_params={"n": 3})
    check("每月倒数第3天：3月29日命中", occurs_on(t_endn, date(2026, 3, 29)))

    t_nth = Task(**base, recur=cfg.RECUR_MONTH_NTH,
                 recur_params={"weekday": 3, "occurrences": [1, -1]})
    check("每月第1个周三：2026-03-04", occurs_on(t_nth, date(2026, 3, 4)))
    check("每月最后1个周三：2026-03-25", occurs_on(t_nth, date(2026, 3, 25)))
    check("每月第1个周三：3月11日不命中", not occurs_on(t_nth, date(2026, 3, 11)))

    t_bi = Task(**base, recur=cfg.RECUR_BIWEEK,
                recur_params={"weekdays": [1], "parity": "odd", "ref_date": "2026-03-02"})
    # 同一周 -> 命中；下一周 -> 不命中；再下一周 -> 命中
    check("单双周：基准周命中", occurs_on(t_bi, date(2026, 3, 2)))
    check("单双周：隔一周不命中", not occurs_on(t_bi, date(2026, 3, 9)))
    check("单双周：再隔一周命中", occurs_on(t_bi, date(2026, 3, 16)))
    # 跨到另一个月也要对
    check("单双周：跨月仍按奇偶", occurs_on(t_bi, date(2026, 3, 30)))

    t_q = Task(**base, recur=cfg.RECUR_QUARTER, recur_params={"mode": "last_day"})
    check("每季度最后一天：3月31日", occurs_on(t_q, date(2026, 3, 31)))
    check("每季度最后一天：6月30日", occurs_on(t_q, date(2026, 6, 30)))
    check("每季度最后一天：5月31日不命中", not occurs_on(t_q, date(2026, 5, 31)))

    t_qw = Task(**base, recur=cfg.RECUR_QUARTER,
                recur_params={"mode": "nth_workday", "n": 1})
    res = next_occurrence(t_qw, after=date(2026, 3, 25))
    check("每季度第1个工作日：2026Q2 是 4月1日(周三)",
          res is not None and res[0] == date(2026, 4, 1), str(res))

    t_work = Task(**base, recur=cfg.RECUR_WORKDAY)
    check("工作日：2026-02-17 春节假期 -> 不命中",
          not occurs_on(t_work, date(2026, 2, 17)))
    check("工作日：2026-02-14 补班周六 -> 命中",
          occurs_on(t_work, date(2026, 2, 14)))
    check("工作日：2026-03-05 普通周四 -> 命中",
          occurs_on(t_work, date(2026, 3, 5)))
    check("工作日：2026-03-07 普通周六 -> 不命中",
          not occurs_on(t_work, date(2026, 3, 7)))

    say("\n=== 3. 节假日顺延 ===")
    # 2026 国庆：10/1(四)~10/7(三) 放假，10/10(六) 补班
    # -> 10/8(四) 是节后第一个工作日，所以 10/1 的任务顺延到 10/8
    t_first = Task(**base, recur=cfg.RECUR_MONTHLY, recur_params={"monthdays": [1]})
    r = next_occurrence(t_first, after=date(2026, 9, 30))
    check("每月1号 + 国庆顺延：10月1日(假期) -> 10月8日(节后第一个工作日)",
          r is not None and r[0] == date(2026, 10, 8), str(r))
    check("顺延返回了原计划日", r is not None and r[1] == date(2026, 10, 1), str(r))

    # 春节 9 天连休：2/15~2/23 放假，2/14 是补班日，2/24 恢复上班
    t_vday = Task(**base, recur=cfg.RECUR_MONTHLY, recur_params={"monthdays": [15]})
    r2 = next_occurrence(t_vday, after=date(2026, 2, 14))
    check("每月15号 + 春节顺延：2月15日(假期首日) -> 2月24日",
          r2 is not None and r2[0] == date(2026, 2, 24), str(r2))

    check("元旦调休 2026-01-04(周日) 算上班日", CALENDAR.is_workday(date(2026, 1, 4)))
    check("劳动节调休 2026-05-09(周六) 算上班日", CALENDAR.is_workday(date(2026, 5, 9)))
    check("国庆前调休 2026-09-20(周日) 算上班日", CALENDAR.is_workday(date(2026, 9, 20)))
    check("2026-10-08 是工作日（假期后第一个工作日）",
          CALENDAR.is_workday(date(2026, 10, 8)))
    check("2026-10-10(周六) 补班算上班日", CALENDAR.is_workday(date(2026, 10, 10)))

    t_daily2 = Task(**base, recur=cfg.RECUR_DAILY,
                    recur_params={"defer_on_holiday": True})
    check("每天 + 顺延：2026-10-03 命中（由10月1日顺延来）",
          holds_on(t_daily2, date(2026, 10, 3)))

    say("\n=== 4. 保存 / 读取 / 完成 / 统计 ===")
    tid = db.add_task(t_daily)
    got = db.get_task(tid)
    check("写入后能读出", got is not None and got.title == "测试")
    got.next_at = compute_next_at(got)
    db.update_fields(tid, next_at=got.next_at)
    check("next_at 已计算", bool(got.next_at), str(got.next_at))

    t2 = Task(title="本周只此一件", times=["10:00"], recur=cfg.RECUR_DAILY, enabled=True)
    tid2 = db.add_task(t2)
    db.mark_done(db.get_task(tid2), day=date.today(), planned=date.today())
    st = stats_mod.day_stats(db, date.today())
    check("今日统计里已完成 >= 1", st["done"] >= 1, str(st))
    check("连续打卡 >= 1 天", stats_mod.streak_days(db) >= 1)
    wk = stats_mod.week_stats(db)
    check("本周统计有 7 天数据", len(wk["daily"]) == 7)
    check("分类占比能算出来", isinstance(stats_mod.category_breakdown(
        db, date.today() - timedelta(days=7), date.today()), list))

    say("\n=== 5. 界面构造（offscreen）===")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    from app.theme import ThemeManager, build_qss, make_icon
    settings = cfg.Settings(tmp / "ui.json")
    theme = ThemeManager(app, settings)
    theme.apply("light")
    check("浅色 QSS 生成", len(build_qss(cfg.LIGHT)) > 1000)
    theme.apply("dark")
    check("深色 QSS 生成", len(build_qss(cfg.DARK)) > 1000)
    theme.apply("light")
    check("图标能画出来", not make_icon(64, cfg.LIGHT).isNull())

    from app.reminder import ReminderScheduler, ensure_chime
    from app.ui.main_window import MainWindow, SettingsDialog
    from app.ui.popup import Popup, PopupManager
    from app.ui.dialogs import TaskEditDialog

    sched = ReminderScheduler(db, settings)
    win = MainWindow(db, sched, theme, settings)
    check("主窗口构造", win is not None)
    win.show()
    app.processEvents()

    # 三个视图 + 页面切换全部用 step() 包住，任何异常都会算失败并打印堆栈
    step("列表视图刷新", lambda: (win.list_view.refresh(), app.processEvents()))
    check("列表里有卡片", win.list_view.vbox.count() > 1)

    def _calendar():
        win.switch_page("calendar")
        app.processEvents()
        assert win.calendar_view.grid.count() > 30, "日历格子没建出来"
        # 抽查一个格子的日期和角标，确认不是空壳
        it = win.calendar_view.grid.itemAtPosition(1, 0)
        assert it is not None and it.widget() is not None, "第1行没有格子"
        cell = it.widget()
        btn = cell.childAt(cell.width() // 2, cell.height() // 2)
        assert btn is not None, "格子里没有按钮"
        assert btn.property("day"), "格子没有绑定日期"
        assert btn.property("badge") is not None, "格子没有角标属性"
    step("日历视图构造 + 刷新", _calendar)

    def _stats():
        win.switch_page("stats")
        app.processEvents()
        assert win.stats_view.bar.data, "柱状图没有数据"
        assert win.stats_view.lbl_week_sum.text(), "本周合计标签是空的"
    step("统计视图刷新（含柱状图/环形图）", _stats)

    step("切回列表页", lambda: (win.switch_page("list"), app.processEvents()))

    def _search():
        win.search.setText("周报")
        app.processEvents()
        assert win.list_view.search_text == "周报"
        win.search.setText("")
        app.processEvents()
    step("搜索过滤", _search)

    def _theme_switch():
        win.toggle_theme()
        app.processEvents()
        win.toggle_theme()
        app.processEvents()
    step("深浅色切换", _theme_switch)

    # 弹窗
    def _popup():
        pop = Popup(db.get_task(tid), theme, catchup=True)
        pop.slide_in(pop.pos())
        app.processEvents()
        assert pop.task.id == tid
        pop._close("test")
        app.processEvents()
    step("提醒弹窗构造与滑入滑出", _popup)

    # 编辑对话框：每种重复规则都构造一遍，确保参数区不会崩
    for key in cfg.RECUR_LABELS:
        t = Task(title="x", recur=key, recur_params={"weekdays": [1], "monthdays": [1],
                                                     "occurrences": [1], "n": 1})
        d = TaskEditDialog(theme, t, db, None)
        d.recur_editor.cmb.setCurrentIndex(d.recur_editor.cmb.findData(key))
        k, p, once = d.recur_editor.collect()
        check(f"重复规则对话框：{key}", k == key, str(p)[:50])
        d.deleteLater()

    d2 = SettingsDialog(theme, settings, db, None)
    check("设置对话框构造", d2 is not None)
    d2.deleteLater()

    # 托盘（offscreen 下可能不可用，不视为失败）
    from app.ui.tray import Tray
    try:
        tray = Tray(theme, settings, sched)
        check("托盘构造", tray is not None)
    except Exception as e:
        print(f"  [skip] 托盘构造（offscreen 环境限制）：{e}")

    chime = ensure_chime(tmp / "chime.wav")
    check("提示音能生成", bool(chime) and Path(chime).exists(),
          f"{Path(chime).stat().st_size if chime else 0} 字节")

    say("\n=== 6. 数据库备份 ===")
    p = db.backup()
    check("备份文件生成", p is not None and Path(p).exists())

    db.close()

    print("\n" + "=" * 60)
    say("\n" + "=" * 60)
    say(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        say("失败项：")
        for f in FAIL:
            say("  - " + f)
    say("=" * 60)
    try:
        REPORT.write_text("\n".join(_report_lines), encoding="utf-8")
    except Exception:
        pass
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
