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

    def _filter_buttons():
        """点一遍筛选按钮。

        之前这里漏测导致一个真 bug 上线：为了显示数量，代码把「全部」按钮的
        文字改成了「全部 12」，而点击处理里又用 sender.text() 去查表，
        于是 KeyError: '全部 12'，点一下就弹错误框。
        所以这里必须真的"点"，不能只调 refresh()。
        """
        from PySide6.QtCore import Qt
        lv = win.list_view
        # 先让按钮文字带上数量（refresh 会改文字）
        lv.refresh()
        app.processEvents()
        assert lv.btn_all.text().startswith("全部"), f"按钮文字异常: {lv.btn_all.text()}"

        for btn, expect in ((lv.btn_todo, "todo"), (lv.btn_done, "done"),
                            (lv.btn_all, "all")):
            btn.click()                     # 真的触发 clicked 信号
            app.processEvents()
            assert lv._filter == expect, \
                f"点「{btn.text()}」后筛选值应为 {expect}，实际 {lv._filter}"
            assert btn.isChecked(), f"点「{btn.text()}」后按钮没变成选中态"
            # 再刷一次，确认没有残留状态导致异常
            lv.refresh()
            app.processEvents()
    step("筛选按钮点击（全部/未完成/已完成）", _filter_buttons)

    # ---- 重复任务完成后必须在第二天恢复（真实用户报的 bug，务必守住）----
    # 病因：complete() 只重算 next_at 没复位 done，而 active_tasks() 条件是
    #       done=0，于是任务永远卡在「已完成」，第二天既不提醒也不回来。
    def _recurring_recovery():
        from app.reminder import ReminderScheduler

        class _S:
            def get(self, k, d=None):
                return {"snooze_minutes": 10}.get(k, d)

            def set(self, *a, **k):
                pass

        today = date.today()
        tomorrow = today + timedelta(days=1)

        rt = Task(title="每天吃维生素", times=["09:00"], recur=cfg.RECUR_DAILY,
                  enabled=True, done=False, next_at=f"{today} 09:00",
                  done_date=None)
        rtid = db.add_task(rt)
        sched2 = ReminderScheduler(db, _S())
        sched2.complete(db.get_task(rtid), planned=today)
        after = db.get_task(rtid)

        # 1) 完成记录要写进 completion_log（统计靠它）
        rows = [dict(r) for r in db.conn.execute(
            "SELECT * FROM completion_log WHERE task_id=?", (rtid,))]
        assert rows and rows[0]["actual_date"] == today.isoformat(), \
            f"完成记录没写进 completion_log: {rows}"
        # 2) done / done_date 要如实记录"今天做完了"。
        #    注意：完成后**不**把 done 抹成 0 —— 那会让 done 与 done_date 打架，
        #    导致「已完成」筛选和「已完成」分组结论相反（用户报过这个问题）。
        #    调度器靠「done=1 且完成日期是过去」判断"新的一天该恢复了"。
        assert after.done is True, f"完成后 done 应为 True，实际 {after.done}"
        assert after.done_date == today.isoformat(), \
            f"完成日期应为今天，实际 {after.done_date}"
        # 3) next_at 必须排到明天之后（不能还是今天）
        assert after.next_at and after.next_at >= f"{tomorrow} 00:00", \
            f"下次提醒没排到明天: {after.next_at}"
        # 4) 模拟"到了第二天"：完成日期变成昨天 -> 调度器要能扫到它
        db.update_fields(rtid, done_date=(today - timedelta(days=1)).isoformat())
        assert any(x.id == rtid for x in db.active_tasks()), \
            "完成日期在过去时，active_tasks() 应该把它视为可提醒（第二天恢复）"
        # 5) 第二天列表里要出现在「今天」而不是「已完成」
        assert win.list_view._group_of(db.get_task(rtid), tomorrow) == "今天", \
            "第二天分组错误，任务没回到「今天」"
        # 6) 一次性的任务完成后应该保持 done 并停用
        once = Task(title="一次性事情", times=["09:00"], recur=cfg.RECUR_NONE,
                    once_date=today.isoformat(), enabled=True, done=False,
                    next_at=f"{today} 09:00")
        oid = db.add_task(once)
        sched2.complete(db.get_task(oid), planned=today)
        o = db.get_task(oid)
        assert o.done is True and o.enabled is False, \
            f"一次性任务完成后的状态不对: done={o.done} enabled={o.enabled}"

        db.delete_task(rtid)
        db.delete_task(oid)
    step("重复任务完成后第二天恢复提醒（真实 bug 回归）", _recurring_recovery)

    # ---- 其他"用户会真实用到、但之前没测过"的状态转换 ----
    # 这几个都是纯逻辑，不依赖界面，出错也只是静默错状态，正是之前漏掉 bug 的盲区。
    def _unmark_done():
        """取消勾选完成：要能恢复成待办，并且重新排下一次提醒。"""
        from app.reminder import ReminderScheduler

        class _S:
            def get(self, k, d=None):
                return {"snooze_minutes": 10}.get(k, d)

            def set(self, *a, **k):
                pass

        today = date.today()
        t = Task(title="取消完成测试", times=["09:00"], recur=cfg.RECUR_DAILY,
                 enabled=True, done=False, next_at=f"{today} 09:00")
        tid = db.add_task(t)
        s = ReminderScheduler(db, _S())
        s.complete(db.get_task(tid), planned=today)
        done_state = db.get_task(tid)
        # 取消完成
        db.unmark_done(done_state)
        db.update_fields(tid, enabled=1,
                         next_at=compute_next_at(db.get_task(tid)))
        back = db.get_task(tid)
        assert back.done is False, "取消完成后 done 没复位"
        assert back.done_date is None, f"取消完成后 done_date 还留着: {back.done_date}"
        assert back.next_at, "取消完成后没有下次提醒时间"
        assert any(x.id == tid for x in db.active_tasks()), \
            "取消完成后调度器扫不到它"
        left = [dict(r) for r in db.conn.execute(
            "SELECT * FROM completion_log WHERE task_id=?", (tid,))]
        assert not left, f"取消完成后 completion_log 里的记录没删掉: {left}"
        db.delete_task(tid)
    step("取消勾选完成后恢复待办", _unmark_done)

    def _skip_once():
        """跳过本次：当天不再出现，但第二天要照常回来。"""
        from app.reminder import ReminderScheduler

        class _S:
            def get(self, k, d=None):
                return {"snooze_minutes": 10}.get(k, d)

            def set(self, *a, **k):
                pass

        today = date.today()
        tomorrow = today + timedelta(days=1)
        t = Task(title="跳过本次测试", times=["09:00"], recur=cfg.RECUR_DAILY,
                 enabled=True, done=False, next_at=f"{today} 09:00")
        tid = db.add_task(t)
        s = ReminderScheduler(db, _S())
        s.skip_once(db.get_task(tid))
        after = db.get_task(tid)
        assert today.isoformat() in (after.skip_dates or []), \
            f"跳过日期没写进 skip_dates: {after.skip_dates}"
        assert after.next_at and after.next_at.startswith(tomorrow.isoformat()), \
            f"跳过后应排到明天，实际 {after.next_at}"
        assert any(x.id == tid for x in db.active_tasks()), \
            "跳过后调度器扫不到它（第二天就回不来了）"
        db.delete_task(tid)
    step("跳过本次：当天跳过、次日恢复", _skip_once)

    def _snooze():
        """稍后提醒：次数累计、提醒时间往后推、完成后清零。"""
        from app.reminder import ReminderScheduler

        class _S:
            def get(self, k, d=None):
                return {"snooze_minutes": 10}.get(k, d)

            def set(self, *a, **k):
                pass

        today = date.today()
        t = Task(title="稍后提醒测试", times=["09:00"], recur=cfg.RECUR_DAILY,
                 enabled=True, done=False, next_at=f"{today} 09:00")
        tid = db.add_task(t)
        s = ReminderScheduler(db, _S())
        s.snooze(db.get_task(tid), 15)
        a = db.get_task(tid)
        assert a.snooze_count == 1, f"稍后次数应为 1，实际 {a.snooze_count}"
        assert a.snooze_total_min == 15, f"累计分钟应为 15，实际 {a.snooze_total_min}"
        assert tid in s._snoozes, "稍后状态没登记到调度器里"
        # 完成后计数应清零
        s.complete(db.get_task(tid), planned=today)
        b = db.get_task(tid)
        assert b.snooze_count == 0 and b.snooze_total_min == 0, \
            f"完成后稍后计数没清零: {b.snooze_count}/{b.snooze_total_min}"
        assert tid not in s._snoozes, "完成后稍后状态没清掉"
        db.delete_task(tid)
    step("稍后提醒：计数累计与清零", _snooze)

    def _extra_times():
        """一天多个时间点：过掉第一个点后，应排到当天的下一个点（而不是明天）。"""
        from app.reminder import ReminderScheduler

        class _S:
            def get(self, k, d=None):
                return {"snooze_minutes": 10}.get(k, d)

            def set(self, *a, **k):
                pass

        today = date.today()
        now = datetime.now()
        # 造两个今天还没到的时间点，最近的排在前面
        later = [h for h in (20, 21, 22, 23) if h > now.hour]
        assert len(later) >= 2, "当前时间太晚，无法构造两个今天未来的时间点"
        times = [f"{later[0]:02d}:00", f"{later[1]:02d}:00"]
        t = Task(title="一天两点测试", times=times, recur=cfg.RECUR_DAILY,
                 enabled=True, done=False,
                 next_at=f"{today} {times[0]}")
        tid = db.add_task(t)
        s = ReminderScheduler(db, _S())
        # 模拟第一个时间点已弹过（_fire 会把下次排到当天下一个点）
        s._fire(db.get_task(tid), datetime.combine(today, datetime.min.time())
                .replace(hour=later[0], minute=0), catchup=False)
        a = db.get_task(tid)
        assert a.next_at == f"{today} {times[1]}", \
            f"应排到当天第二个时间点 {times[1]}，实际 {a.next_at}"
        # 再弹一次之后，应该排到明天第一个点
        s._fire(db.get_task(tid), datetime.combine(today, datetime.min.time())
                .replace(hour=later[1], minute=0), catchup=False)
        b = db.get_task(tid)
        assert b.next_at.startswith((today + timedelta(days=1)).isoformat()), \
            f"当天时间点用完后应排到明天，实际 {b.next_at}"
        db.delete_task(tid)
    step("一天多时间点：依次用完后才排到明天", _extra_times)

    def _startup_quiet():
        """关机一晚后开机：不补弹过期提醒，但任务要保持「未完成」。

        场景：昨晚 23:00 关机，有条当天 09:00 的提醒没点完成；
        第二天开机时它已经过去十几个小时，不该弹窗刷屏，
        但也不能被标成已完成 —— 界面里要能看到它还没做。
        """
        from app.reminder import ReminderScheduler

        class _S:
            def get(self, k, d=None):
                return {"snooze_minutes": 10}.get(k, d)

            def set(self, *a, **k):
                pass

        # 造一条"昨天 09:00 就该提醒、但没完成"的任务
        yesterday = datetime.now() - timedelta(days=1)
        overdue_at = f"{yesterday.date()} 09:00"
        t = Task(title="昨晚错过的提醒", times=["09:00"], recur=cfg.RECUR_DAILY,
                 enabled=True, done=False, next_at=overdue_at,
                 created_at=overdue_at)
        tid = db.add_task(t)

        fired = []
        s = ReminderScheduler(db, _S())
        s.due.connect(lambda task, when, cu: fired.append((task.id, cu)))
        s.tick(force_catchup=True)          # 模拟"刚开机"

        after = db.get_task(tid)
        assert not any(i == tid for i, _ in fired), \
            f"开机时不该为过期提醒弹窗，但它弹了: {fired}"
        assert after.done is False, "被跳过的过期提醒不该被标成已完成"
        assert after.next_at and after.next_at > \
            datetime.now().strftime("%Y-%m-%d %H:%M"), \
            f"跳过后应把下次提醒排到未来，实际 {after.next_at}"
        assert any(x.id == tid for x in db.active_tasks()), \
            "跳过后任务应该还在待办列表里（界面要显示未完成）"

        # 对照一：开机时刚错过 20 分钟（在 60 分钟静默阈值内）-> 要补提醒
        recent_at = (datetime.now() - timedelta(minutes=20)).strftime("%Y-%m-%d %H:%M")
        t3 = Task(title="刚错过二十分钟", times=["09:00"], recur=cfg.RECUR_DAILY,
                  enabled=True, done=False, next_at=recent_at, created_at=recent_at)
        tid3 = db.add_task(t3)
        fired.clear()
        s3 = ReminderScheduler(db, _S())
        s3.due.connect(lambda task, when, cu: fired.append((task.id, cu)))
        s3.tick(force_catchup=True)         # 也是"刚开机"
        assert any(i == tid3 for i, _ in fired), \
            "开机时错过 20 分钟（未超静默阈值）应该补提醒，但没弹"
        assert any(cu for i, cu in fired if i == tid3), \
            "这种情况应该是「补提醒」（catchup=True）"

        # 对照二：电脑一直开着、到点刚过 2 分钟 -> 应该正常弹（不是启动路径）
        soon_at = (datetime.now() - timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M")
        t2 = Task(title="刚过去两分钟", times=["09:00"], recur=cfg.RECUR_DAILY,
                  enabled=True, done=False, next_at=soon_at, created_at=soon_at)
        tid2 = db.add_task(t2)
        fired.clear()
        s2 = ReminderScheduler(db, _S())
        s2.due.connect(lambda task, when, cu: fired.append((task.id, cu)))
        s2.tick(force_catchup=False)        # 正常运行中的一次扫描
        assert any(i == tid2 for i, _ in fired), \
            "正常运行中刚过期 2 分钟的提醒应该照常弹"

        for x in (tid, tid2, tid3):
            db.conn.execute("DELETE FROM completion_log WHERE task_id=?", (x,))
            db.delete_task(x)
    step("关机一晚后开机：不刷屏、保持未完成", _startup_quiet)

    def _bar_geometry():
        """柱状图几何约束（用户报过"填充跑到圈外"的 bug，现在底槽已去掉）。

        教训：前两版这个测试都假通过 ——
          1. 只校验辅助函数 bar_radius()，改坏绘制处照样通过；
          2. 靠像素"可见性"猜填充边界，判据太间接。
        所以现在读绘制函数**实际记录下来的矩形和半径**来断言。
        """
        from app.ui.views import MiniBarChart, bar_geometry, bar_radius

        # 1) 半径约束：永不超过矩形短边的一半（画出来才是药丸形，不会横向鼓出）
        for w in (560.0, 700.0, 900.0, 1120.0):
            geo = bar_geometry(w, 180.0, 7)
            bw, max_h = geo["bw"], geo["max_h"]
            for ratio in (0.05, 0.2, 0.33, 0.5, 0.8, 1.0):
                bh = max_h * ratio
                r = bar_radius(bw, bh)
                assert r <= min(bw, bh) / 2 + 1e-9, \
                    f"w={w} ratio={ratio}: 半径 {r} 超过短边一半"
                assert r <= bw / 2 + 1e-9, \
                    f"w={w} ratio={ratio}: 半径 {r} 超过宽度一半（会横向鼓出去）"

        # 2) 真渲染，检查绘制函数实际使用的几何
        chart = MiniBarChart(theme)
        chart.resize(700, 180)
        chart.show()
        for ratio in (0.0, 0.1, 0.33, 0.5, 0.8, 1.0):
            chart.set_data([{"label": "一", "done": 1, "total": 3, "rate": ratio,
                             "is_today": False, "is_future": False}])
            for _ in range(4):
                app.processEvents()
            drawn = chart.last_drawn
            assert len(drawn) == 1, f"绘制记录条数不对: {len(drawn)}"
            item = drawn[0]
            bw, base_y, max_h = item["bw"], item["base_y"], item["max_h"]

            if ratio <= 0.0 or max_h * ratio < 2:
                # 0% 的情况不画填充柱，但要有占位小圆点（否则那天完全看不见）
                assert item["fill"] is None, f"ratio={ratio}: 不该画填充柱"
                assert item["stub_h"] > 0, "0% 那天没有占位小圆点，会看不见"
            else:
                fx, fy, fw, fh = item["fill"]
                assert abs(fw - bw) < 0.01, \
                    f"ratio={ratio}: 柱宽 {fw} 与列宽 {bw} 不一致"
                # 关键：半径不超过宽度一半，柱子就不会比列宽、不会横向鼓出去
                assert item["fill_r"] <= fw / 2 + 1e-9, \
                    f"ratio={ratio}: 半径 {item['fill_r']} 超过柱宽一半"
                # 高度要如实反映比例
                assert abs(fh - max_h * ratio) < 0.5, \
                    f"ratio={ratio}: 柱高 {fh} 与比例不符（应为 {max_h * ratio:.1f}）"
                # 底部对齐基线，且不超出可用高度
                assert abs((fy + fh) - base_y) < 0.01, \
                    f"ratio={ratio}: 柱子底部没对齐基线"
                assert fy >= 0, f"ratio={ratio}: 柱子顶部跑到画布外"
        chart.hide()
    step("柱状图几何：半径不超短边一半、比例与基线正确", _bar_geometry)

    def _advance_keeps_today():
        """弹过提醒后重排，不能把"今天还没到的时间点"整个跳过。

        真实 bug：任务「每天 21:17」在早上被触发（补提醒）后，重排逻辑
        一律从明天开始找，于是当天 21:17 被跳过 —— 用户当天就再也收不到
        提醒了，列表里当天也看不到这条任务。
        """
        from app.reminder import ReminderScheduler

        class _S:
            def get(self, k, d=None):
                return {"snooze_minutes": 10}.get(k, d)

            def set(self, *a, **k):
                pass

        now = datetime.now()
        # 需要"今天还有一个未来的时间点"，太晚就跑不了这个用例
        if now.hour >= 22:
            raise AssertionError("当前时间太晚，无法构造今天的未来时间点")

        later = f"{now.hour + 1:02d}:{(now.minute // 5) * 5:02d}"
        today = date.today()
        t = Task(title="今天晚些时候", times=[later], recur=cfg.RECUR_DAILY,
                 enabled=True, done=False, next_at=f"{today} {later}")
        tid = db.add_task(t)
        s = ReminderScheduler(db, _S())
        # 模拟"刚开机，发现这条已经过期"（force_catchup 且早于_STARTUP_SILENT 之外）
        # 直接调 _advance 更直接：它就是这个场景里被调用的重排函数
        s._advance(db.get_task(tid), now)
        after = db.get_task(tid)
        assert after.next_at and after.next_at.startswith(today.isoformat()), (
            f"重排后应保留今天的 {later}，实际 {after.next_at}"
            f"（说明今天剩余的时间点被跳过了）")
        assert after.next_at.endswith(later), \
            f"重排后时间点应为 {later}，实际 {after.next_at}"
        assert after.done is False

        # 对照：已完成的任务重排时不该回到今天
        t2 = Task(title="今天已完成", times=[later], recur=cfg.RECUR_DAILY,
                  enabled=True, done=True, done_date=today.isoformat(),
                  next_at=f"{today} {later}")
        tid2 = db.add_task(t2)
        s._advance(db.get_task(tid2), now)
        after2 = db.get_task(tid2)
        assert after2.next_at is None or not after2.next_at.startswith(today.isoformat()), \
            f"已完成的任务不该排回今天，实际 {after2.next_at}"

        for x in (tid, tid2):
            db.conn.execute("DELETE FROM completion_log WHERE task_id=?", (x,))
            db.delete_task(x)
    step("弹过提醒后重排：保留今天剩余的时间点", _advance_keeps_today)

    def _filter_and_group():
        """标签筛选与「已完成」分组的判定（用户报过"任务看起来丢了"的两处）。

        场景一：任务有 done_date=今天（刚点过完成又取消），但 next_at 还在今天
                -> 不该被归到「已完成」，否则圆圈是空的却显示在已完成组。
        场景二：开了标签筛选后，没有标签的任务被静默过滤 —— 必须有提示条写明原因。
        """
        lv = win.list_view
        today = date.today()
        tomorrow = today + timedelta(days=1)

        # 场景一：完成状态、筛选、分组必须给出一致结论
        #   新语义：done 表示"本轮做完了"，done_date 表示"哪天做完的"。
        #   今天完成的 -> 显示「已完成」；完成日期在过去 -> 新的一天应恢复。
        t = Task(title="今天刚完成", times=["20:00"], recur=cfg.RECUR_DAILY,
                 enabled=True, done=True, done_date=today.isoformat(),
                 next_at=f"{tomorrow} 20:00")
        tid = db.add_task(t)
        g = lv._group_of(db.get_task(tid), today)
        assert g == "已完成", f"今天完成的应归「已完成」，实际「{g}」"
        # 「已完成」筛选必须也能看到它（曾经因为 done 被抹成 0 而看不到）
        lv.set_tag_filter([])
        lv._filter = "done"
        assert lv._matches(db.get_task(tid)), \
            "今天完成的任务必须能被「已完成」筛选看到（否则筛选会显示空）"
        lv._filter = "todo"
        assert not lv._matches(db.get_task(tid)), \
            "今天完成的任务不该出现在「未完成」筛选里"
        # 完成日期在过去 -> 新的一天，应回到待办、出现在「今天」
        db.update_fields(tid, done_date=(today - timedelta(days=1)).isoformat())
        lv._filter = "todo"
        assert lv._matches(db.get_task(tid)), "昨天完成的任务今天应算未完成"
        lv._filter = "done"
        assert not lv._matches(db.get_task(tid)), "昨天完成的任务今天不该算已完成"
        lv._filter = "all"
        # 对照：取消完成后（done=0）应显示在「今天」
        db.update_fields(tid, done=0, done_date=None, next_at=f"{today} 20:00")
        g2 = lv._group_of(db.get_task(tid), today)
        assert g2 == "今天", f"取消完成后应归「今天」，实际「{g2}」"
        db.delete_task(tid)

        # 场景二：标签筛选的匹配要忽略大小写，且有提示条
        t2 = Task(title="带标签任务", times=["20:00"], recur=cfg.RECUR_DAILY,
                  enabled=True, done=False, tags=["Work"],
                  next_at=f"{today} 20:00")
        tid2 = db.add_task(t2)
        lv.set_tag_filter(["work"])           # 故意用小写
        assert lv._matches(db.get_task(tid2)), \
            "标签匹配应忽略大小写，否则用户手输标签会导致任务被静默过滤"
        assert lv.filter_hint.isVisible() or not lv.filter_hint.isHidden(), \
            "有标签筛选时应显示提示条（否则用户会以为任务丢了）"
        assert "work" in lv.lbl_filter_hint.text().lower(), \
            f"提示条没写清筛了什么: {lv.lbl_filter_hint.text()!r}"

        # 点「清除筛选」应恢复
        lv._clear_tag_filter()
        assert lv.tag_filter == [], "清除筛选后 tag_filter 应为空"
        assert lv.filter_hint.isHidden() or not lv.filter_hint.isVisible(), \
            "清除筛选后提示条应隐藏"
        db.delete_task(tid2)

        # 顺带：筛选条件不会把没标签的任务误判为匹配
        t3 = Task(title="没标签", times=["20:00"], recur=cfg.RECUR_DAILY,
                  enabled=True, done=False, tags=[], next_at=f"{today} 20:00")
        tid3 = db.add_task(t3)
        lv.set_tag_filter(["重要"])
        assert not lv._matches(db.get_task(tid3)), \
            "没标签的任务在筛选时不该被匹配"
        lv.set_tag_filter([])
        assert lv._matches(db.get_task(tid3)), "清空筛选后应恢复匹配"
        db.delete_task(tid3)
    step("标签筛选提示 + 已完成分组判定", _filter_and_group)

    def _no_stray_windows():
        """点筛选/标签时不能有"游离的可见顶层窗口"。

        真实 bug：清理旧控件时用了 w.setParent(None) 再 deleteLater()。
        setParent(None) 会把控件变成顶层窗口，而 deleteLater 是延迟销毁的 ——
        如果控件当时是可见的，它就会在屏幕上以独立小窗口闪一下（用户报过"点击后
        一闪而过的空白小框"，很影响观感）。
        正确顺序是 hide() -> setParent(None) -> deleteLater()。
        这里就用"有没有可见的游离顶层控件"来守住这个不变量。
        """
        from PySide6.QtWidgets import QPushButton
        from app.ui.main_window import MainWindow

        def visible_strays():
            out = []
            for w in QApplication.topLevelWidgets():
                if w is win or not w.isVisible():
                    continue
                out.append(f"{type(w).__name__}({w.objectName()!r}) "
                           f"{w.width()}x{w.height()}")
            return out

        assert not visible_strays(), f"启动时就有游离可见窗口: {visible_strays()}"

        # 点标签
        for b in win.sidebar.tag_holder.findChildren(QPushButton):
            if b.text() == "全部标签":
                continue
            b.click()
            for _ in range(5):
                app.processEvents()
            bad = visible_strays()
            assert not bad, f"点标签「{b.text()}」后出现游离可见窗口: {bad}"

        # 点筛选按钮
        lv = win.list_view
        for btn in (lv.btn_todo, lv.btn_done, lv.btn_all):
            btn.click()
            for _ in range(5):
                app.processEvents()
            bad = visible_strays()
            assert not bad, f"点筛选「{btn.text()}」后出现游离可见窗口: {bad}"

        # 切到日历（日历里也会重建卡片和格子）
        win.switch_page("calendar")
        for _ in range(6):
            app.processEvents()
        bad = visible_strays()
        assert not bad, f"切到日历后出现游离可见窗口: {bad}"
        win.switch_page("list")
        for _ in range(4):
            app.processEvents()
        win.sidebar._toggle_tag("__all__")      # 恢复无筛选
        for _ in range(4):
            app.processEvents()
    step("没有游离的可见顶层窗口（防闪烁回归）", _no_stray_windows)

    def _done_state_consistency():
        """判定函数 / 分组 / 卡片上的勾，三处必须一致。

        真实 bug：分组用了"今天是否完成"的判定，卡片却仍直接看 task.done，
        于是"昨天打了勾的每日任务"在「今天」组里却画着勾、还写着昨天的时间，
        用户看到会以为今天已经做完了（实际并没有）。
        """
        from app.ui.widgets import TaskCard, is_done_for

        today = date.today()
        yesterday = today - timedelta(days=1)
        cases = [
            ("今天完成", True, today.isoformat(), True),
            ("昨天完成（每日任务应回到待办）", True, yesterday.isoformat(), False),
            ("未完成", False, None, False),
            ("未完成但有残留完成日期", False, today.isoformat(), False),
        ]
        lv = win.list_view
        for label, done, done_date, expect in cases:
            t = Task(title=f"一致性-{label}", times=["20:00"], recur=cfg.RECUR_DAILY,
                     enabled=True, done=done, done_date=done_date,
                     next_at=f"{today} 20:00")
            tid = db.add_task(t)
            fresh = db.get_task(tid)
            got = is_done_for(fresh)
            assert got is expect, f"{label}: is_done_for 应为 {expect}，实际 {got}"
            card = TaskCard(fresh, theme)
            assert card.check.isChecked() is expect, (
                f"{label}: 卡片勾选={card.check.isChecked()} 与判定 {expect} 不一致")
            assert (card.objectName() == "CardDone") is expect, (
                f"{label}: 卡片样式={card.objectName()} 与判定 {expect} 不一致")
            grp = lv._group_of(fresh, today)
            assert (grp == "已完成") is expect, (
                f"{label}: 分组={grp} 与判定 {expect} 不一致")
            lv.set_tag_filter([])
            lv._filter = "done"
            assert lv._matches(fresh) is expect, f"{label}: 「已完成」筛选结论不一致"
            lv._filter = "todo"
            assert lv._matches(fresh) is (not expect), f"{label}: 「未完成」筛选结论不一致"
            lv._filter = "all"
            card.deleteLater()
            db.conn.execute("DELETE FROM completion_log WHERE task_id=?", (tid,))
            db.delete_task(tid)
    step("完成判定/分组/卡片勾选三处一致", _done_state_consistency)

    def _uncheck_no_popup():
        """取消勾选「完成」后：任务留在今天，而且不该因此补弹提醒。

        真实 bug：取消完成时用 compute_next_at() 重排，它会把"今天已经过去的
        时间点"算回来，于是任务立刻变成"已过期"，调度器马上补弹一次 ——
        用户看到的就是"勾选完成后又取消，弹窗又冒出来了"。
        """
        from app.reminder import ReminderScheduler

        class _S:
            def get(self, k, d=None):
                return {"snooze_minutes": 10}.get(k, d)

            def set(self, *a, **k):
                pass

        now = datetime.now()
        today = date.today()
        past = (now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M")
        t = Task(title="取消完成测试", times=[past[11:]], recur=cfg.RECUR_DAILY,
                 enabled=True, done=False, next_at=past, created_at=past)
        tid = db.add_task(t)
        s = ReminderScheduler(db, _S())
        # 模拟"今天已经为它弹过一次"
        db.log_fire(tid, past, "popup", "shown")
        s._pending.add(tid)

        fired = []
        s.due.connect(lambda task, when, cu: fired.append(task.id))

        # 勾选完成
        s.complete(db.get_task(tid), planned=today)
        # 取消完成
        s.unmark_done_reschedule(db.get_task(tid))
        after = db.get_task(tid)

        assert after.done is False, "取消完成后 done 应为 False"
        assert (after.next_at or "")[:10] == today.isoformat(), \
            f"取消完成后应留在今天（否则任务从今天的列表里消失了）: {after.next_at}"
        assert win.list_view._group_of(after, today) in ("今天", "逾期"), \
            "取消完成后应显示在今天的列表里"

        # 跑几次调度，不该补弹
        for _ in range(5):
            s.tick(force_catchup=False)
        assert not fired, f"取消完成不该触发补弹，但弹了: {fired}"

        db.conn.execute("DELETE FROM completion_log WHERE task_id=?", (tid,))
        db.conn.execute("DELETE FROM fire_log WHERE task_id=?", (tid,))
        db.delete_task(tid)
    step("取消勾选后留在今天且不补弹", _uncheck_no_popup)

    def _filter_buttons_after_refresh():
        """按钮文字被改成带数量之后，再点一次 —— 就是当初崩掉的场景。"""
        lv = win.list_view
        lv.refresh()
        app.processEvents()
        lv.btn_all.click()
        app.processEvents()
        assert lv._filter == "all"
        lv.btn_todo.click()
        app.processEvents()
        assert lv._filter == "todo"
    step("按钮文字带数量后再点击（回归测试）", _filter_buttons_after_refresh)

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

    def _dialog_tag_chips():
        """编辑对话框里用"点选胶囊"选标签（不再手打逗号分隔）。"""
        from PySide6.QtWidgets import QPushButton
        t = Task(title="标签点选测试", times=["09:00"], recur=cfg.RECUR_DAILY,
                 tags=["重要"])
        d = TaskEditDialog(theme, t, db, None)
        d.show()
        for _ in range(5):
            app.processEvents()

        def chips():
            return {b.text(): b for b in d.tag_box.findChildren(QPushButton)}

        assert chips().get("重要") is not None, "标签胶囊没建出来"
        assert chips()["重要"].isChecked(), "任务已有的标签应默认选中"
        assert not chips()["紧急"].isChecked(), "未选中的标签不该是勾选态"

        # 点选 / 取消
        chips()["紧急"].click()
        for _ in range(3):
            app.processEvents()
        assert "紧急" in d.selected_tags(), "点胶囊后应加入已选"
        chips()["重要"].click()
        for _ in range(3):
            app.processEvents()
        assert "重要" not in d.selected_tags(), "再点一次应取消选中"

        # 新增标签
        d.edit_new_tag.setText("测试新标签")
        d._add_new_tag()
        for _ in range(3):
            app.processEvents()
        assert "测试新标签" in d.selected_tags(), "新增标签后应被选中"
        assert "测试新标签" in chips(), "新增的标签应出现在胶囊里"

        # 保存后要带进任务对象
        d._save()
        assert d.result_task().tags == d.selected_tags(), \
            f"保存后的标签与已选不一致: {d.result_task().tags} vs {d.selected_tags()}"
        d.deleteLater()
        try:
            db.delete_tag("测试新标签")      # 清理测试标签，别污染用户的标签列表
        except Exception:
            pass
    step("编辑对话框：标签用点选胶囊", _dialog_tag_chips)

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
