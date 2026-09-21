"""生成示例数据（纯粹为了看效果和试功能，可以随时删掉）。

用法：
    python tools/seed_data.py            # 写进 E:\\Memo\\data\\memo.db
    python tools/seed_data.py --demo     # 写进 E:\\Memo\\data\\demo.db（不碰正式数据）
    python tools/seed_data.py --clear    # 清空任务
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def seed(db) -> int:
    """造一批有代表性的示例任务：覆盖各种重复规则、优先级、完成状态。"""
    from app import config as cfg
    from app.models import Task

    today = date.today()
    samples = [
        Task(title="吃维生素", times=["08:30"], priority="中", category="健康",
             tags=["例行"], recur=cfg.RECUR_DAILY, note="饭后吃，别空腹",
             next_at=f"{today} 08:30"),
        Task(title="交周报", times=["09:00"], priority="高", category="工作",
             tags=["重要"], recur=cfg.RECUR_WEEKLY, recur_params={"weekdays": [5]},
             note="写清楚本周进度、风险、下周计划，发群里并抄送组长",
             next_at=f"{today} 09:00"),
        Task(title="给妈妈打电话", times=["20:00"], priority="中", category="生活",
             recur=cfg.RECUR_BIWEEK,
             recur_params={"weekdays": [5], "parity": "odd", "ref_date": "2026-01-05"},
             next_at=f"{today} 20:00", snooze_count=1),
        Task(title="还信用卡", times=["10:00"], priority="高", category="生活",
             recur=cfg.RECUR_MONTH_END_N, recur_params={"n": 3},
             next_at=f"{today} 10:00",
             deferred_from=(today - timedelta(days=2)).isoformat()),
        Task(title="季度复盘文档", times=["14:00"], priority="低", category="工作",
             recur=cfg.RECUR_QUARTER, recur_params={"mode": "last_day"},
             next_at=f"{today} 14:00"),
        Task(title="晨跑 3 公里", times=["07:00"], priority="低", category="健康",
             recur=cfg.RECUR_WORKDAY, done=True,
             completed_at=f"{today} 07:12:00", done_date=today.isoformat(),
             next_at=f"{today} 07:00"),
        Task(title="读完《认知觉醒》第 4 章", times=["21:30"], priority="低",
             category="学习", recur=cfg.RECUR_DAILY, done=True,
             completed_at=f"{today} 21:40:00", done_date=today.isoformat(),
             next_at=f"{today} 21:30"),
        Task(title="交房租", times=["09:00"], priority="高", category="生活",
             recur=cfg.RECUR_MONTHLY, recur_params={"monthdays": [1]},
             next_at=f"{today + timedelta(days=2)} 09:00"),
        Task(title="（一次性）下周三取快递", times=["18:30"], priority="中",
             category="生活", recur=cfg.RECUR_NONE,
             once_date=(today + timedelta(days=3)).isoformat(),
             next_at=f"{today + timedelta(days=3)} 18:30"),
    ]
    n = 0
    for i, t in enumerate(samples):
        t.created_at = f"{today} 00:00:{i:02d}"
        tid = db.add_task(t)
        n += 1
        if t.done:
            db.mark_done(db.get_task(tid), day=today - timedelta(days=i % 4), planned=today)
        for tag in t.tags:
            db.add_tag(tag)

    # 历史完成记录，统计页才有东西看
    for i in range(1, 15):
        d = today - timedelta(days=i)
        for j in range((i % 3) + 1):
            t = Task(title=f"历史事项 {i}-{j}", times=["09:00"],
                     category=["工作", "生活", "学习"][j % 3],
                     recur=cfg.RECUR_DAILY, done=True,
                     completed_at=f"{d} 09:{30 + j * 5}:00",
                     done_date=d.isoformat(), created_at=f"{d} 08:00:00")
            db.mark_done(t, day=d, planned=d)
            n += 1
    return n


def main() -> int:
    from app import config as cfg
    from app.database import Database

    db_path = cfg.DB_PATH
    if "--demo" in sys.argv:
        db_path = cfg.DATA_DIR / "demo.db"
    db = Database(db_path)
    print("数据库：", db_path)

    if "--clear" in sys.argv:
        db.reset_all()
        print("已清空所有任务")
    else:
        n = seed(db)
        print(f"已写入 {n} 条示例数据")
    print("任务总数：", len(db.all_tasks()))
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
