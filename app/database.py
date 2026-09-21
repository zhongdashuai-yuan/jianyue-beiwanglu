"""SQLite 数据访问层。

只在这个文件里写 SQL，界面层不碰 SQL。
数据库结构变化时：在 _SCHEMA 里加表，在 _migrate() 里加 ALTER TABLE。
"""

from __future__ import annotations

import shutil
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

from . import config as cfg
from .models import Tag, Task, now_str

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    title           TEXT NOT NULL,
    note            TEXT DEFAULT '',
    priority        TEXT DEFAULT '中',
    category        TEXT DEFAULT '工作',
    tags            TEXT DEFAULT '[]',
    times           TEXT DEFAULT '["09:00"]',
    lead_minutes    INTEGER DEFAULT 0,
    recur           TEXT DEFAULT 'daily',
    recur_params    TEXT DEFAULT '{}',
    once_date       TEXT,
    skip_dates      TEXT DEFAULT '[]',
    enabled         INTEGER DEFAULT 1,
    next_at         TEXT,
    deferred_from   TEXT,
    last_fired_at   TEXT,
    extra_fired_at  TEXT,
    done            INTEGER DEFAULT 0,
    completed_at    TEXT,
    done_date       TEXT,
    snooze_count    INTEGER DEFAULT 0,
    snooze_total_min INTEGER DEFAULT 0,
    created_at      TEXT,
    sort_order      INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_tasks_next   ON tasks(next_at);
CREATE INDEX IF NOT EXISTS idx_tasks_done   ON tasks(done);
CREATE INDEX IF NOT EXISTS idx_tasks_recur  ON tasks(recur);

CREATE TABLE IF NOT EXISTS tags (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT UNIQUE NOT NULL,
    color       TEXT DEFAULT '#12B5C9',
    sort_order  INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS categories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT UNIQUE NOT NULL,
    color       TEXT DEFAULT '#12B5C9',
    sort_order  INTEGER DEFAULT 0
);

-- 完成日志：统计完成率/打卡天数的唯一数据来源
CREATE TABLE IF NOT EXISTS completion_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id         INTEGER,
    title           TEXT,
    category        TEXT,
    planned_date    TEXT NOT NULL,      -- 原计划哪一天的事
    actual_date     TEXT NOT NULL,      -- 实际哪天完成的
    completed_at    TEXT NOT NULL,
    overdue_days    INTEGER DEFAULT 0,
    was_deferred    INTEGER DEFAULT 0,
    UNIQUE(task_id, planned_date)
);
CREATE INDEX IF NOT EXISTS idx_log_actual ON completion_log(actual_date);

CREATE TABLE IF NOT EXISTS settings (
    key     TEXT PRIMARY KEY,
    value   TEXT
);

-- 日志式记录每次提醒弹出，方便排查「为什么没提醒我」
CREATE TABLE IF NOT EXISTS fire_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     INTEGER,
    fired_at    TEXT,
    planned_at  TEXT,
    kind        TEXT,          -- popup / system / snooze / catchup
    result      TEXT           -- done / snoozed / skipped / auto_closed
);
CREATE INDEX IF NOT EXISTS idx_fire_task ON fire_log(task_id);
"""


class Database:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or cfg.DB_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.init_schema()

    # ------------------------------------------------------------------ 基础
    @contextmanager
    def tx(self):
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def init_schema(self) -> None:
        self.conn.executescript(_SCHEMA)
        self.conn.commit()
        self._seed_defaults()
        self._migrate()

    def _migrate(self) -> None:
        """老库升级用：缺列就补。"""
        have = {r["name"] for r in self.conn.execute("PRAGMA table_info(tasks)")}
        wanted = {
            "note": "TEXT DEFAULT ''", "priority": "TEXT DEFAULT '中'",
            "category": "TEXT DEFAULT '工作'", "tags": "TEXT DEFAULT '[]'",
            "times": "TEXT DEFAULT '[\"09:00\"]'", "lead_minutes": "INTEGER DEFAULT 0",
            "recur_params": "TEXT DEFAULT '{}'", "once_date": "TEXT",
            "skip_dates": "TEXT DEFAULT '[]'", "enabled": "INTEGER DEFAULT 1",
            "next_at": "TEXT", "deferred_from": "TEXT", "last_fired_at": "TEXT",
            "extra_fired_at": "TEXT", "done": "INTEGER DEFAULT 0",
            "completed_at": "TEXT", "done_date": "TEXT",
            "snooze_count": "INTEGER DEFAULT 0",
            "snooze_total_min": "INTEGER DEFAULT 0",
            "created_at": "TEXT", "sort_order": "INTEGER DEFAULT 0",
        }
        for col, ddl in wanted.items():
            if col not in have:
                self.conn.execute(f"ALTER TABLE tasks ADD COLUMN {col} {ddl}")
        self.conn.commit()

    def _seed_defaults(self) -> None:
        cur = self.conn.execute("SELECT COUNT(*) c FROM categories")
        if cur.fetchone()["c"] == 0:
            for i, c in enumerate(cfg.DEFAULT_CATEGORIES):
                self.conn.execute(
                    "INSERT OR IGNORE INTO categories(name,color,sort_order) VALUES(?,?,?)",
                    (c["name"], c["color"], i))
        cur = self.conn.execute("SELECT COUNT(*) c FROM tags")
        if cur.fetchone()["c"] == 0:
            for i, name in enumerate(["重要", "紧急", "例行"]):
                self.conn.execute(
                    "INSERT OR IGNORE INTO tags(name,color,sort_order) VALUES(?,?,?)",
                    (name, ["#12B5C9", "#FF7A85", "#7ED0A8"][i], i))
        self.conn.commit()

    def close(self) -> None:
        try:
            self.conn.commit()
            self.conn.close()
        except Exception:
            pass

    # ------------------------------------------------------------------ 任务
    def add_task(self, t: Task) -> int:
        row = t.to_row()
        cols = ", ".join(row)
        ph = ", ".join("?" for _ in row)
        with self.tx() as c:
            cur = c.execute(f"INSERT INTO tasks({cols}) VALUES({ph})", list(row.values()))
            tid = cur.lastrowid
        t.id = tid
        return tid

    def update_task(self, t: Task) -> None:
        if t.id is None:
            raise ValueError("update_task 需要 id")
        row = t.to_row()
        sets = ", ".join(f"{k}=?" for k in row)
        with self.tx() as c:
            c.execute(f"UPDATE tasks SET {sets} WHERE id=?", [*row.values(), t.id])

    def update_fields(self, task_id: int, **fields) -> None:
        """只改几个字段（调度器高频用）。"""
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        with self.tx() as c:
            c.execute(f"UPDATE tasks SET {sets} WHERE id=?", [*fields.values(), task_id])

    def delete_task(self, task_id: int) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM tasks WHERE id=?", (task_id,))

    def get_task(self, task_id: int) -> Task | None:
        r = self.conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return Task.from_row(r) if r else None

    def all_tasks(self, include_done: bool = True) -> list[Task]:
        sql = "SELECT * FROM tasks"
        if not include_done:
            sql += " WHERE done=0"
        sql += " ORDER BY sort_order ASC, id ASC"
        return [Task.from_row(r) for r in self.conn.execute(sql)]

    def active_tasks(self) -> list[Task]:
        """还没完成、且启用了的任务（调度器主查询）。"""
        return [Task.from_row(r) for r in self.conn.execute(
            "SELECT * FROM tasks WHERE done=0 AND enabled=1 ORDER BY next_at ASC, id ASC")]

    def tasks_with_next_at(self) -> list[Task]:
        return [Task.from_row(r) for r in self.conn.execute(
            "SELECT * FROM tasks WHERE done=0 AND enabled=1 AND next_at IS NOT NULL "
            "ORDER BY next_at ASC")]

    # ------------------------------------------------------- 完成 / 撤销完成
    def mark_done(self, task: Task, day: date | None = None,
                  planned: date | None = None, was_deferred: bool = False) -> None:
        day = day or date.today()
        planned = planned or day
        overdue = max(0, (day - planned).days)
        with self.tx() as c:
            c.execute(
                "UPDATE tasks SET done=1, completed_at=?, done_date=?, "
                "snooze_count=0, snooze_total_min=0 WHERE id=?",
                (now_str(), day.isoformat(), task.id))
            c.execute(
                "INSERT OR REPLACE INTO completion_log"
                "(task_id,title,category,planned_date,actual_date,completed_at,"
                " overdue_days,was_deferred) VALUES(?,?,?,?,?,?,?,?)",
                (task.id, task.title, task.category, planned.isoformat(),
                 day.isoformat(), now_str(), overdue, 1 if was_deferred else 0))

    def unmark_done(self, task: Task) -> None:
        with self.tx() as c:
            c.execute("UPDATE tasks SET done=0, completed_at=NULL, done_date=NULL "
                      "WHERE id=?", (task.id,))
            c.execute("DELETE FROM completion_log WHERE task_id=? AND planned_date=?",
                      (task.id, task.done_date or date.today().isoformat()))

    def archive_done_before(self, before: date) -> int:
        """把很久以前完成的任务清掉（可选功能，避免库无限大）。"""
        with self.tx() as c:
            cur = c.execute("DELETE FROM tasks WHERE done=1 AND done_date < ?",
                            (before.isoformat(),))
            return cur.rowcount

    def unfinished_today(self, day: date | None = None) -> list[Task]:
        day = day or date.today()
        rows = self.conn.execute(
            "SELECT * FROM tasks WHERE done=0 AND enabled=1 "
            "AND (deferred_from=? OR substr(next_at,1,10)=?)",
            (day.isoformat(), day.isoformat()))
        return [Task.from_row(r) for r in rows]

    # ------------------------------------------------------------------ 标签
    def tags(self) -> list[Tag]:
        return [Tag.from_row(r) for r in
                self.conn.execute("SELECT * FROM tags ORDER BY sort_order, id")]

    def add_tag(self, name: str, color: str = "#12B5C9") -> None:
        with self.tx() as c:
            c.execute("INSERT OR IGNORE INTO tags(name,color,sort_order) "
                      "VALUES(?,?,(SELECT COALESCE(MAX(sort_order),0)+1 FROM tags))",
                      (name, color))

    def delete_tag(self, name: str) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM tags WHERE name=?", (name,))

    # ------------------------------------------------------------------ 分类
    def categories(self) -> list[dict]:
        return [dict(r) for r in
                self.conn.execute("SELECT * FROM categories ORDER BY sort_order, id")]

    def add_category(self, name: str, color: str = "#12B5C9") -> None:
        with self.tx() as c:
            c.execute("INSERT OR IGNORE INTO categories(name,color,sort_order) "
                      "VALUES(?,?,(SELECT COALESCE(MAX(sort_order),0)+1 FROM categories))",
                      (name, color))

    def rename_category(self, old: str, new: str, color: str | None = None) -> None:
        with self.tx() as c:
            c.execute("UPDATE categories SET name=? WHERE name=?", (new, old))
            if color:
                c.execute("UPDATE categories SET color=? WHERE name=?", (color, new))
            c.execute("UPDATE tasks SET category=? WHERE category=?", (new, old))

    def delete_category(self, name: str) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM categories WHERE name=?", (name,))

    # ------------------------------------------------------------------ 日志
    def log_fire(self, task_id: int | None, planned_at: str | None,
                 kind: str, result: str = "") -> None:
        with self.tx() as c:
            c.execute("INSERT INTO fire_log(task_id,fired_at,planned_at,kind,result) "
                      "VALUES(?,?,?,?,?)",
                      (task_id, now_str(), planned_at, kind, result))

    def last_fires(self, limit: int = 100) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM fire_log ORDER BY id DESC LIMIT ?", (limit,))]

    # ------------------------------------------------------- 键值 setting 表
    def kv_get(self, key: str, default: str | None = None) -> str | None:
        r = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

    def kv_set(self, key: str, value: str) -> None:
        with self.tx() as c:
            c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, value))

    # ------------------------------------------------------------------ 维护
    def backup(self) -> Path | None:
        """复制一份带时间戳的备份到 E:\\Memo\\data\\backup\\。"""
        try:
            cfg.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            dst = cfg.BACKUP_DIR / f"memo_{datetime.now():%Y%m%d_%H%M%S}.db"
            self.conn.commit()
            shutil.copy2(self.path, dst)
            # 只留最近 20 份
            old = sorted(cfg.BACKUP_DIR.glob("memo_*.db"))
            for f in old[:-20]:
                f.unlink(missing_ok=True)
            return dst
        except Exception:
            return None

    def reset_all(self) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM tasks")
            c.execute("DELETE FROM completion_log")
            c.execute("DELETE FROM fire_log")
