"""复现「每天提醒的任务，第二天不恢复」这个问题。

用法：
    python tools/repro_recurring_done.py

它做这些事（全程用临时数据库，不碰你的正式数据）：
  1. 建一条"每天 09:00"的任务
  2. 模拟用户点「完成」
  3. 检查：done 标志、next_at、是否还能被调度器扫到
  4. 模拟跨天（把调度器的"今天"往后推一天）
  5. 再检查一遍：第二天它会不会恢复提醒
"""

from __future__ import annotations

import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

OUT = Path(__file__).resolve().parent / "_repro_report.txt"
lines: list[str] = []


def say(msg: str) -> None:
    lines.append(msg)
    try:
        print(msg)
    except UnicodeEncodeError:
        try:
            print(msg.encode("gbk", "replace").decode("gbk", "replace"))
        except Exception:
            pass


def main() -> int:
    from app import config as cfg
    from app.database import Database
    from app.models import Task
    from app.reminder import ReminderScheduler

    tmp = Path(tempfile.mkdtemp(prefix="memo_repro_"))
    db = Database(tmp / "repro.db")

    today = date.today()
    tomorrow = today + timedelta(days=1)

    t = Task(title="每天吃维生素", times=["09:00"], priority="中", category="健康",
             recur=cfg.RECUR_DAILY, enabled=True, done=False,
             next_at=f"{today} 09:00",
             created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    tid = db.add_task(t)
    say("=" * 70)
    say(f"今天 = {today}   明天 = {tomorrow}")
    say(f"建了一条任务：每天 09:00 提醒（id={tid}）")
    say(f"  初始状态: done={db.get_task(tid).done}  next_at={db.get_task(tid).next_at}")
    say("-" * 70)

    class FakeSettings:
        def get(self, k, d=None):
            return {"snooze_minutes": 10, "defer_on_holiday": True}.get(k, d)

        def set(self, *a, **k):
            pass

    sched = ReminderScheduler(db, FakeSettings())

    # ---- 1. 用户点「完成」 ----
    say("① 用户点「完成」")
    task = db.get_task(tid)
    sched.complete(task, planned=today)
    st = db.get_task(tid)
    say(f"   done={st.done}  done_date={st.done_date}  completed_at={st.completed_at}")
    say(f"   next_at={st.next_at}")
    fired = [f for f in db.last_fires(20) if f["task_id"] == tid]
    say(f"   fire_log: {[(f['kind'], f['result']) for f in fired]}")

    # 完成记录存在 completion_log 表里（统计靠它），
    # done 标志对重复任务会被立即复位（为了让第二天能再提醒），所以这里查日志表。
    log_rows = [dict(r) for r in db.conn.execute(
        "SELECT * FROM completion_log WHERE task_id=?", (tid,))]
    logged = bool(log_rows) and log_rows[0]["actual_date"] == today.isoformat()
    say(f"   completion_log: {[(r['planned_date'], r['actual_date']) for r in log_rows]}")
    ok1 = logged and st.done_date == today.isoformat()
    say(f"   [{'通过' if ok1 else '失败'}] 完成记录已写入（统计能统计到）")
    # 重复任务的 done 应该是 False（已排下次），一次性的才是 True
    ok1b = st.done is False
    say(f"   [{'通过' if ok1b else '失败'}] 重复任务的 done 已复位（明天才能再提醒）")
    ok2 = bool(st.next_at) and st.next_at.startswith(tomorrow.isoformat())
    say(f"   [{'通过' if ok2 else '失败'}] 下次提醒已排到明天 ({st.next_at})")

    # ---- 2. 第二天：调度器能扫到它吗？ ----
    say("-" * 70)
    say("② 模拟跨天到第二天，看调度器能不能扫到这条任务")
    active = db.active_tasks()
    found = [x for x in active if x.id == tid]
    say(f"   active_tasks() 返回 {len(active)} 条")
    say(f"   其中包含这条任务吗: {'是' if found else '否 ← 不会被提醒'}")
    say(f"   （active_tasks 的 SQL 条件是 done=0 AND enabled=1）")
    ok3 = bool(found)
    say(f"   [{'通过' if ok3 else '失败'}] 跨天后仍能被调度器扫到")

    # ---- 3. 第二天跨天钩子跑完之后的状态 ----
    say("-" * 70)
    say("③ 模拟调度器跨天处理 _on_new_day（第二天 00:00 触发）")
    tomorrow_dt = datetime.combine(tomorrow, datetime.min.time()) + timedelta(hours=8)
    sched._on_new_day(tomorrow_dt)
    st2 = db.get_task(tid)
    say(f"   done={st2.done}  next_at={st2.next_at}")
    ok4 = (st2.done is False)
    say(f"   [{'通过' if ok4 else '失败'}] 第二天 done 标志已复位（能再次提醒）")
    ok5 = bool(st2.next_at) and st2.next_at.startswith(tomorrow.isoformat())
    say(f"   [{'通过' if ok5 else '失败'}] 第二天 next_at 指向当天 ({st2.next_at})")

    # ---- 4. 列表视图会把它分到哪一组 ----
    say("-" * 70)
    say("④ 列表视图分组（第二天看，用户眼里它出现在哪）")

    def group_of(task, ref: date) -> str:
        # 与 app/ui/views.py 的 ListView._group_of 同逻辑
        if task.done:
            return "已完成"
        if not task.enabled:
            return "已停用"
        nd = task.next_date
        if nd is None:
            return "以后"
        if nd < ref:
            return "逾期"
        if nd == ref:
            return "今天"
        if nd == ref + timedelta(days=1):
            return "明天"
        return "以后"

    say(f"   第二天分组: {group_of(st2, tomorrow)}")
    ok6 = group_of(st2, tomorrow) in ("今天", "逾期")
    say(f"   [{'通过' if ok6 else '失败'}] 第二天出现在「今天」（用户能看到并收到提醒）")

    say("=" * 70)
    checks = [("完成记录已写入", ok1), ("done 已复位", ok1b), ("排到明天", ok2),
              ("跨天可被扫到", ok3), ("done 复位", ok4),
              ("next_at 指向当天", ok5), ("第二天显示在今天", ok6)]
    allok = all(o for _, o in checks)
    say("复现结论：" + ("全部通过，问题已修复" if allok else "★ 仍有失败项 ★"))
    if not allok:
        say("失败环节: " + "、".join(n for n, o in checks if not o))
    say("=" * 70)

    db.close()
    OUT.write_text("\n".join(lines), encoding="utf-8")
    return 0 if allok else 1


if __name__ == "__main__":
    sys.exit(main())
