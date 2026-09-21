"""统计：完成率 / 连续打卡 / 分类占比。

数据全部来自 completion_log（谁在计划日完成了什么）+ tasks（今天该做什么）。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from .database import Database
from .models import Task
from .recurrence import holds_on


def expected_on(db: Database, day: date, tasks: list[Task] | None = None) -> list[Task]:
    """某一天「本来该做」的任务（含节假日顺延后的）。"""
    tasks = tasks if tasks is not None else db.all_tasks()
    out = []
    for t in tasks:
        if holds_on(t, day):
            out.append(t)
    return out


def done_on(db: Database, day: date) -> int:
    r = db.conn.execute(
        "SELECT COUNT(*) c FROM completion_log WHERE actual_date=?", (day.isoformat(),)
    ).fetchone()
    return r["c"] if r else 0


def completion_log_between(db: Database, start: date, end: date) -> list[dict]:
    return [dict(r) for r in db.conn.execute(
        "SELECT * FROM completion_log WHERE actual_date BETWEEN ? AND ? "
        "ORDER BY actual_date", (start.isoformat(), end.isoformat()))]


def day_stats(db: Database, day: date, tasks: list[Task] | None = None) -> dict:
    """今日概览：应该做几件 / 完成几件 / 逾期几件 / 完成率。"""
    tasks = tasks if tasks is not None else db.all_tasks()
    exp = expected_on(db, day, tasks)
    total = len(exp)
    done_ids = {r["task_id"] for r in db.conn.execute(
        "SELECT task_id FROM completion_log WHERE actual_date=?", (day.isoformat(),))}
    done = sum(1 for t in exp if t.id in done_ids or t.done_date == day.isoformat())
    overdue = sum(1 for t in exp
                  if not (t.id in done_ids or t.done_date == day.isoformat())
                  and t.next_at and t.next_at < f"{day.isoformat()} 23:59")
    rate = (done / total) if total else 0.0
    return {"total": total, "done": done, "left": max(0, total - done),
            "overdue": overdue, "rate": rate, "date": day.isoformat()}


def week_stats(db: Database, ref: date | None = None, tasks: list[Task] | None = None) -> dict:
    """本周完成率（周一到周日）。"""
    ref = ref or date.today()
    monday = ref - timedelta(days=ref.weekday())
    tasks = tasks if tasks is not None else db.all_tasks()
    daily = []
    tot = dn = 0
    for i in range(7):
        d = monday + timedelta(days=i)
        st = day_stats(db, d, tasks)
        # 未来的日子不算进总分母，否则周一早上完成率永远是 20%
        if d <= ref:
            tot += st["total"]
            dn += st["done"]
        daily.append({"date": d.isoformat(), "label": ["一", "二", "三", "四", "五", "六", "日"][i],
                      "total": st["total"], "done": st["done"], "rate": st["rate"],
                      "is_future": d > ref, "is_today": d == ref})
    return {"monday": monday.isoformat(), "daily": daily,
            "total": tot, "done": dn, "rate": (dn / tot) if tot else 0.0}


def streak_days(db: Database, tasks: list[Task] | None = None) -> int:
    """连续打卡天数：从今天往回数，每天都有「完成记录」才算。

    今天还没完成不算断（否则早上打开就显示 0 天，很打击人）。
    """
    tasks = tasks if tasks is not None else db.all_tasks()
    today = date.today()
    streak = 0
    cur = today
    # 今天：有完成记录就算上，没有也不打断
    r = db.conn.execute("SELECT COUNT(*) c FROM completion_log WHERE actual_date=?",
                        (cur.isoformat(),)).fetchone()
    if r and r["c"] > 0:
        streak += 1
    for i in range(1, 400):
        cur = today - timedelta(days=i)
        r = db.conn.execute("SELECT COUNT(*) c FROM completion_log WHERE actual_date=?",
                            (cur.isoformat(),)).fetchone()
        cnt = r["c"] if r else 0
        has_planned = bool(expected_on(db, cur, tasks))
        if cnt > 0:
            streak += 1
        elif has_planned:
            break          # 那天有任务却没完成 -> 断了
        # 那天本来就没任务 -> 跳过，不影响连续
    return streak


def category_breakdown(db: Database, start: date, end: date) -> list[dict]:
    """分类占比：一段时间的完成数量分布。"""
    rows = db.conn.execute(
        "SELECT category, COUNT(*) c FROM completion_log "
        "WHERE actual_date BETWEEN ? AND ? GROUP BY category ORDER BY c DESC",
        (start.isoformat(), end.isoformat())).fetchall()
    total = sum(r["c"] for r in rows) or 1
    colors = {c["name"]: c["color"] for c in db.categories()}
    return [{"name": r["category"] or "未分类", "count": r["c"],
             "ratio": r["c"] / total,
             "color": colors.get(r["category"], "#12B5C9")} for r in rows]


def overdue_stats(db: Database, days: int = 30) -> dict:
    """最近一段时间逾期完成的情况。"""
    end = date.today()
    start = end - timedelta(days=days)
    rows = db.conn.execute(
        "SELECT overdue_days FROM completion_log WHERE actual_date BETWEEN ? AND ?",
        (start.isoformat(), end.isoformat())).fetchall()
    n = len(rows)
    late = sum(1 for r in rows if (r["overdue_days"] or 0) > 0)
    avg = (sum(r["overdue_days"] or 0 for r in rows) / n) if n else 0.0
    return {"total": n, "late": late, "avg_overdue": avg,
            "on_time_rate": ((n - late) / n) if n else 0.0}


def weekday_completion(db: Database, weeks: int = 8) -> list[dict]:
    """按星期几看完成习惯（周一~周日各完成了多少件）。"""
    end = date.today()
    start = end - timedelta(days=weeks * 7)
    buckets: dict[int, int] = defaultdict(int)
    for r in db.conn.execute(
            "SELECT actual_date FROM completion_log WHERE actual_date BETWEEN ? AND ?",
            (start.isoformat(), end.isoformat())):
        try:
            d = date.fromisoformat(r["actual_date"])
        except Exception:
            continue
        buckets[d.weekday()] += 1
    mx = max(buckets.values()) if buckets else 1
    return [{"weekday": i, "label": ["一", "二", "三", "四", "五", "六", "日"][i],
             "count": buckets.get(i, 0), "ratio": buckets.get(i, 0) / mx}
            for i in range(7)]


def export_csv(db: Database, path) -> int:
    """导出全部任务为 CSV（Excel 可直接打开，用 UTF-8 BOM 防乱码）。"""
    import csv
    tasks = db.all_tasks()
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["标题", "备注", "优先级", "分类", "标签", "提醒时间",
                    "重复规则", "下次提醒", "已完成", "完成时间", "创建时间"])
        for t in tasks:
            w.writerow([t.title, t.note, t.priority, t.category,
                        "、".join(t.tags), t.times_text(), t.repeat_text(),
                        t.next_at or "", "是" if t.done else "否",
                        t.completed_at or "", t.created_at])
    return len(tasks)


def export_json(db: Database, path) -> int:
    """导出为 JSON（可用于备份 / 迁移）。"""
    import json
    data = {"tasks": [t.to_row() | {"id": t.id} for t in db.all_tasks()],
            "tags": [{"name": g.name, "color": g.color} for g in db.tags()],
            "categories": db.categories()}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return len(data["tasks"])
