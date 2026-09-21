"""数据模型：Task（任务）/ Tag（标签）。

设计说明（改代码前先看这里）：
  * 数据库里 task 的 recurrence / recur_params / times / tags / skip_dates
    都存成 JSON 文本，读出来在这里变成 Python 对象。
  * next_at 是「下一次该提醒的时间」，由 recurrence 引擎算完后写回数据库，
    调度器只读 next_at，不用每次重算，这样启动就快。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, time, timedelta
from typing import Any

from . import config as cfg


def _parse_hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def _fmt_time(t: time) -> str:
    return f"{t.hour:02d}:{t.minute:02d}"


@dataclass
class Task:
    id: int | None = None
    title: str = ""
    note: str = ""
    priority: str = "中"
    category: str = "工作"
    tags: list[str] = field(default_factory=list)

    # 提醒时间点（HH:MM 字符串列表）。第一个是主时间，其余是同日追加提醒。
    times: list[str] = field(default_factory=lambda: ["09:00"])
    lead_minutes: int = 0            # 提前几分钟提醒（0=准点）

    # 重复规则
    recur: str = cfg.RECUR_DAILY
    recur_params: dict[str, Any] = field(default_factory=dict)
    # 一次性任务用这个日期
    once_date: str | None = None

    skip_dates: list[str] = field(default_factory=list)   # 「跳过本次」
    enabled: bool = True

    # 调度用的缓存字段
    next_at: str | None = None            # "YYYY-MM-DD HH:MM"
    deferred_from: str | None = None      # 因为节假日被顺延时，记录原计划日期
    last_fired_at: str | None = None      # 上次弹提醒的时间（含稍后）
    extra_fired_at: str | None = None     # 追加时间点的发送记录 "date|HH:MM"

    # 完成状态
    done: bool = False
    completed_at: str | None = None
    done_date: str | None = None          # 属于哪一天的任务（用于统计）
    snooze_count: int = 0
    snooze_total_min: int = 0
    created_at: str = ""
    sort_order: int = 0

    # ---------------- 序列化 ----------------
    @staticmethod
    def dumps(v: Any) -> str:
        return json.dumps(v, ensure_ascii=False)

    @staticmethod
    def loads(v: Any, default: Any = None):
        if v in (None, ""):
            return default
        if isinstance(v, (list, dict)):
            return v
        try:
            return json.loads(v)
        except Exception:
            return default

    @classmethod
    def from_row(cls, row) -> "Task":
        """sqlite3.Row -> Task"""
        d = dict(row)
        return cls(
            id=d.get("id"),
            title=d.get("title") or "",
            note=d.get("note") or "",
            priority=d.get("priority") or "中",
            category=d.get("category") or "工作",
            tags=cls.loads(d.get("tags"), []) or [],
            times=cls.loads(d.get("times"), ["09:00"]) or ["09:00"],
            lead_minutes=d.get("lead_minutes") or 0,
            recur=d.get("recur") or cfg.RECUR_DAILY,
            recur_params=cls.loads(d.get("recur_params"), {}) or {},
            once_date=d.get("once_date"),
            skip_dates=cls.loads(d.get("skip_dates"), []) or [],
            enabled=bool(d.get("enabled", 1)),
            next_at=d.get("next_at"),
            deferred_from=d.get("deferred_from"),
            last_fired_at=d.get("last_fired_at"),
            extra_fired_at=d.get("extra_fired_at"),
            done=bool(d.get("done", 0)),
            completed_at=d.get("completed_at"),
            done_date=d.get("done_date"),
            snooze_count=d.get("snooze_count") or 0,
            snooze_total_min=d.get("snooze_total_min") or 0,
            created_at=d.get("created_at") or "",
            sort_order=d.get("sort_order") or 0,
        )

    def to_row(self) -> dict:
        return {
            "title": self.title,
            "note": self.note,
            "priority": self.priority,
            "category": self.category,
            "tags": self.dumps(self.tags),
            "times": self.dumps(self.times),
            "lead_minutes": int(self.lead_minutes or 0),
            "recur": self.recur,
            "recur_params": self.dumps(self.recur_params or {}),
            "once_date": self.once_date,
            "skip_dates": self.dumps(self.skip_dates),
            "enabled": 1 if self.enabled else 0,
            "next_at": self.next_at,
            "deferred_from": self.deferred_from,
            "last_fired_at": self.last_fired_at,
            "extra_fired_at": self.extra_fired_at,
            "done": 1 if self.done else 0,
            "completed_at": self.completed_at,
            "done_date": self.done_date,
            "snooze_count": int(self.snooze_count or 0),
            "snooze_total_min": int(self.snooze_total_min or 0),
            "created_at": self.created_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "sort_order": int(self.sort_order or 0),
        }

    # ---------------- 便捷属性 ----------------
    @property
    def first_time(self) -> time:
        try:
            return _parse_hhmm(self.times[0])
        except Exception:
            return time(9, 0)

    @property
    def next_dt(self) -> datetime | None:
        if not self.next_at:
            return None
        try:
            return datetime.strptime(self.next_at, "%Y-%m-%d %H:%M")
        except Exception:
            return None

    @property
    def next_date(self) -> date | None:
        dt = self.next_dt
        return dt.date() if dt else None

    def is_done_for(self, day: date) -> bool:
        """这一天是否已完成（统计和列表都用它）。"""
        if not self.done:
            return False
        return self.done_date == day.isoformat()

    def times_as_time(self) -> list[time]:
        out = []
        for s in self.times or ["09:00"]:
            try:
                out.append(_parse_hhmm(s))
            except Exception:
                pass
        return sorted(out) or [time(9, 0)]

    def times_text(self) -> str:
        return " / ".join(_fmt_time(t) for t in self.times_as_time())

    def repeat_text(self) -> str:
        """给卡片显示的一行重复说明，例如「每周一、周三 · 生活」。"""
        from .recurrence import describe
        return describe(self)

    def is_deferred(self) -> bool:
        return bool(self.deferred_from and self.next_at
                    and not self.next_at.startswith(self.deferred_from))

    def copy(self) -> "Task":
        return Task(**{**asdict(self)})


@dataclass
class Tag:
    id: int | None = None
    name: str = ""
    color: str = "#12B5C9"
    sort_order: int = 0

    @classmethod
    def from_row(cls, row) -> "Tag":
        d = dict(row)
        return cls(id=d.get("id"), name=d.get("name") or "",
                   color=d.get("color") or "#12B5C9",
                   sort_order=d.get("sort_order") or 0)


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today() -> date:
    return date.today()


def combine(day: str | date, hhmm: str) -> datetime:
    if isinstance(day, str):
        y, m, d = (int(x) for x in day.split("-"))
        day = date(y, m, d)
    t = _parse_hhmm(hhmm)
    return datetime.combine(day, t)
